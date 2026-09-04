// SPDX-License-Identifier: MIT
using AgentBoard.Contracts;
using AgentBoard.Domain.Workflow;
using AgentBoard.Domain.Workflow.Durable;
using AgentBoard.Infrastructure.Persistence.Workflow;
using Xunit;

namespace AgentBoard.Infrastructure.Tests.Durable;

/// <summary>
/// A1 exit criteria that can only be proven through a real durable store:
/// crash windows never yield "confirmed but unrecorded" work, recovery is
/// queryable from the registry, and dedup/fencing survive the restart
/// (doc 150 NFR-001, NFR-005, PR-002, PR-007, PR-008).
/// </summary>
public sealed class PlaneStoreTests : IDisposable
{
    private readonly string _dbPath = Path.Combine(
        Path.GetTempPath(), $"agentboard-plane-{Guid.NewGuid():N}.db");

    public void Dispose()
    {
        Microsoft.Data.Sqlite.SqliteConnection.ClearAllPools();
        foreach (var suffix in new[] { "", "-wal", "-shm" })
        {
            var path = _dbPath + suffix;
            if (File.Exists(path))
            {
                File.Delete(path);
            }
        }
    }

    [Fact]
    public void Fresh_store_has_no_durable_state_a_crash_could_confirm_against()
    {
        using var store = new SqlitePlaneStore(_dbPath);
        Assert.False(store.HasDurableState());
        Assert.Null(store.Load());
    }

    [Fact]
    public void Accepted_outcome_survives_process_restart_and_stays_single()
    {
        var clock = new TestClock();
        var plane = new DurableServerPlane(clock.Now, NewId);
        var fixture = Setup.Run(plane, clock);

        fixture.Develop(clock, plane, out var result);

        using (var store = new SqlitePlaneStore(_dbPath))
        {
            store.Commit(plane);
        }

        // A new process recovers the full position of the run (PR-002).
        using var revivedStore = new SqlitePlaneStore(_dbPath);
        var state = revivedStore.Load()
            ?? throw new InvalidOperationException("committed state must be loadable");
        var revived = DurableServerPlane.Restore(clock.Now, NewId, state);

        var snapshot = revived.Registry.Snapshot("run-1")!;
        Assert.Equal(StageRunState.Succeeded, snapshot.Stages.Single().Stage.State);
        Assert.NotNull(snapshot.Stages.Single().Executions.Single().Outcome);

        // Redelivery after the restart is still a duplicate, not a second
        // outcome (NFR-001 + PR-007).
        var duplicate = revived.Results.Process(result);
        Assert.Equal(ResultOutcomeKind.Duplicate, duplicate.Kind);
        Assert.Single(revived.Registry.RequireExecution(fixture.ExecutionId).Attempts);
    }

    [Fact]
    public void Result_processed_before_commit_window_is_replayed_once_after_recovery()
    {
        var clock = new TestClock();
        using var store = new SqlitePlaneStore(_dbPath);

        var plane = new DurableServerPlane(clock.Now, NewId);
        var fixture = Setup.Run(plane, clock);
        var assignment = fixture.DispatchDev(plane);
        store.Commit(plane); // the command is durable: the Node may receive it

        // The result arrives and is applied in memory, but the Server dies
        // before the commit — the "authoritative update without a record"
        // state must be impossible: nothing about this result is durable.
        var result = fixture.ResultRaw(plane, assignment, AttemptResultStatus.Succeeded, summary: "dev done");
        Assert.Equal(ResultOutcomeKind.Accepted, plane.Results.Process(result).Kind);

        using var recovery = new SqlitePlaneStore(_dbPath);
        var committed = recovery.Load()!;
        var revived = DurableServerPlane.Restore(clock.Now, NewId, committed);

        Assert.Null(revived.Registry.RequireExecution(fixture.ExecutionId).Outcome);

        // The broker redelivers the un-ACKed result; recovery accepts it once
        // from the last durable point, and a further duplicate stays duplicate.
        Assert.Equal(ResultOutcomeKind.Accepted, revived.Results.Process(result).Kind);
        Assert.NotNull(revived.Registry.RequireExecution(fixture.ExecutionId).Outcome);
        Assert.Equal(ResultOutcomeKind.Duplicate, revived.Results.Process(result).Kind);
        Assert.Single(revived.Registry.RequireExecution(fixture.ExecutionId).Attempts);
    }

    [Fact]
    public void Outbox_progress_is_durable_and_never_republished_after_confirm()
    {
        var clock = new TestClock();
        var plane = new DurableServerPlane(clock.Now, NewId);
        var fixture = Setup.Run(plane, clock);
        fixture.DispatchDev(plane);

        using (var store = new SqlitePlaneStore(_dbPath))
        {
            store.Commit(plane);
        }

        using var reopened = new SqlitePlaneStore(_dbPath);
        var revived = DurableServerPlane.Restore(clock.Now, NewId, reopened.Load()!);
        var message = Assert.Single(revived.Outbox.Messages);
        Assert.Equal(OutboxState.Pending, message.State);

        var transport = new CountingTransport();
        var dispatcher = new OutboxDispatcher(
            revived.Outbox, transport, revived.Planner, revived.DeadLetters, clock.Now);
        Assert.Equal(1, dispatcher.DispatchDue());
        reopened.Commit(revived);

        var final = DurableServerPlane.Restore(clock.Now, NewId, reopened.Load()!);
        Assert.Equal(OutboxState.Confirmed, Assert.Single(final.Outbox.Messages).State);
        Assert.Equal(0, new OutboxDispatcher(final.Outbox, transport, final.Planner, final.DeadLetters, clock.Now).DispatchDue());
        Assert.Equal(1, transport.PublishCount);
    }

    [Fact]
    public void Lease_fencing_survives_the_restart()
    {
        var clock = new TestClock();
        var plane = new DurableServerPlane(clock.Now, NewId);
        var fixture = Setup.Run(plane, clock);
        var first = fixture.DispatchDev(plane);

        // Supersede with epoch 2 before "crashing".
        var second = plane.Dispatcher.Dispatch(
            fixture.ExecutionId, "worker-2", "agent.dev", new[] { "development" }, "policy-rev-1",
            TimeSpan.FromMinutes(10));
        using (var store = new SqlitePlaneStore(_dbPath))
        {
            store.Commit(plane);
        }

        using var reopened = new SqlitePlaneStore(_dbPath);
        var revived = DurableServerPlane.Restore(clock.Now, NewId, reopened.Load()!);

        Assert.Equal(2, revived.Leases.CurrentFor(fixture.ExecutionId)!.LeaseEpoch);
        Assert.Equal(LeaseVerdict.StaleEpoch, revived.Leases.Check(first.AssignmentId, first.LeaseEpoch));

        // The old node's late result is rejected even though its attempt id
        // still exists after restore (PR-008, doc 151 §4.2 invariant 5).
        var stale = revived.Results.Process(
            fixture.ResultRaw(revived, first, AttemptResultStatus.Succeeded, summary: "late from epoch 1"));
        Assert.Equal(ResultOutcomeKind.RejectedStaleEpoch, stale.Kind);
        Assert.Null(revived.Registry.RequireExecution(fixture.ExecutionId).Outcome);
        Assert.NotNull(second);
    }

    [Fact]
    public void Audit_trail_is_queryable_after_recovery()
    {
        var clock = new TestClock();
        var plane = new DurableServerPlane(clock.Now, NewId);
        var fixture = Setup.Run(plane, clock);
        fixture.Develop(clock, plane, out _);

        using var store = new SqlitePlaneStore(_dbPath);
        store.Commit(plane);
        var revived = DurableServerPlane.Restore(clock.Now, NewId, store.Load()!);

        Assert.Contains(revived.Registry.Audit.Records, r => r.Action == "outcome.accepted");
        Assert.Contains(revived.Registry.Audit.Records, r => r.Action == "stage.transition");
    }

    // -----------------------------------------------------------------
    // Deterministic helpers (mirrors Domain.Tests' PlaneFixture at the
    // minimal size these tests need).
    // -----------------------------------------------------------------

    private static int _ids;

    private static string NewId() => (++_ids).ToString("D4");

    private sealed class TestClock
    {
        public DateTimeOffset NowValue = new(2026, 9, 4, 0, 0, 0, TimeSpan.Zero);

        public Func<DateTimeOffset> Now => () => NowValue;

        public void Advance(int minutes) => NowValue = NowValue.AddMinutes(minutes);
    }

    private sealed class CountingTransport : ICommandTransport
    {
        public int PublishCount;

        public PublishResult Publish(OutboxMessage message)
        {
            PublishCount++;
            return PublishResult.Confirmed;
        }
    }

    private sealed class Setup
    {
        public string ExecutionId { get; } = "exec-dev-1";

        public static Setup Run(DurableServerPlane plane, TestClock clock)
        {
            var nodes = new[]
            {
                Node(StageType.Development, StageType.Review),
                Node(StageType.Review, StageType.Development, StageType.Qa),
                Node(StageType.Qa),
            };
            var version = new WorkflowVersion(
                "version-golden", "definition-golden", 1, "workflow.v1",
                nodes, WorkflowGraph.ComputeContentHash(nodes));

            plane.Registry.PublishVersion(version);
            plane.Registry.CreateRun("run-1", version.VersionId);
            plane.Registry.MoveRun("run-1", WorkflowRunState.Queued, Ctx("q"));
            plane.Registry.MoveRun("run-1", WorkflowRunState.Running, Ctx("r"));
            plane.Registry.AddStage("run-1", "stg-dev-1", StageType.Development, 1, null);
            plane.Registry.AddExecution("stg-dev-1", "exec-dev-1");
            return new Setup();
        }

        public Assignment DispatchDev(DurableServerPlane plane) => plane.Dispatcher.Dispatch(
            ExecutionId, "worker-1", "agent.dev", new[] { "development" }, "policy-rev-1",
            TimeSpan.FromMinutes(10));

        public void Develop(TestClock clock, DurableServerPlane plane, out ResultEnvelope result)
        {
            var assignment = DispatchDev(plane);
            result = ResultRaw(plane, assignment, AttemptResultStatus.Succeeded, summary: "dev done");
            var verdict = plane.Results.Process(result);
            Assert.Equal(ResultOutcomeKind.Accepted, verdict.Kind);
        }

        public ResultEnvelope ResultRaw(
            DurableServerPlane plane,
            Assignment assignment,
            AttemptResultStatus status,
            FailureCategory failure = FailureCategory.None,
            string? summary = null)
        {
            // The Server enforces the causal tie to the issued command, so a
            // well-formed result must name the message it answers.
            var command = plane.Sent.TryGet(assignment.AssignmentId, out var issued) ? issued : null;

            return new ResultEnvelope
            {
                MessageId = $"msg-{NewId()}",
                SchemaVersion = "result.v1",
                MessageType = MessageTypes.ExecutionResult,
                CorrelationId = assignment.WorkflowRunId,
                CausationId = command?.MessageId ?? "cmd-unknown",
                IdempotencyKey = command?.IdempotencyKey ?? $"{assignment.AssignmentId}:{assignment.AttemptId}",
                WorkflowRunId = assignment.WorkflowRunId,
                StageRunId = assignment.StageRunId,
                ExecutionId = assignment.ExecutionId,
                AttemptId = assignment.AttemptId,
                AssignmentId = assignment.AssignmentId,
                WorkerId = assignment.WorkerId,
                AgentId = assignment.AgentId,
                LeaseEpoch = assignment.LeaseEpoch,
                ResultStatus = status,
                FailureCategory = failure,
                OutcomeSummary = summary,
                Traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
                CreatedAt = DateTimeOffset.UtcNow,
            };
        }

        public ResultEnvelope Result(AttemptResultStatus status, DurableServerPlane plane, string summary)
        {
            var assignment = plane.Leases.CurrentFor(ExecutionId)!;
            return ResultRaw(plane, assignment, status, summary: summary);
        }

        private static WorkflowNode Node(StageType stage, params StageType[] transitions) => new(
            stage.ToString().ToLowerInvariant(), stage, stage.ToString().ToLowerInvariant(),
            "{}", "{}", transitions, "retry-standard", "policy-golden",
            new StageBudget(3600, 600), true);

        private static TransitionContext Ctx(string reason) =>
            new("test-harness", reason, SchemaVersions.Registry);
    }
}
