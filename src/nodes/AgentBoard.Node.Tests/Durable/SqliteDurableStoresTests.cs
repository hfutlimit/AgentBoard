// SPDX-License-Identifier: MIT
using AgentBoard.Contracts;
using AgentBoard.Node.Durable;
using Xunit;

namespace AgentBoard.Node.Tests.Durable;

/// <summary>
/// Proves the Node's durable trio (journal, event store, result outbox) is
/// real storage: state survives the process, dedup keys survive restart, and
/// a crashed-before-publish result is re-adopted instead of lost
/// (doc 151 §6.2; A2 exit criteria, second-round review item "memory-only").
/// </summary>
public sealed class SqliteDurableStoresTests : IDisposable
{
    private const string Worker = "worker-s";
    private const string Trace = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01";

    private readonly string _dbPath = Path.Combine(
        Path.GetTempPath(), $"agentboard-node-{Guid.NewGuid():N}.db");

    private DateTimeOffset _now = new(2026, 9, 4, 0, 0, 0, TimeSpan.Zero);

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

    private static CommandEnvelope Assign(
        string messageId,
        string idempotency,
        string execution = "exec-1",
        string attempt = "att-1",
        long epoch = 1,
        DateTimeOffset? expiresAt = null,
        DateTimeOffset? issuedAt = null)
    {
        var issued = issuedAt ?? new DateTimeOffset(2026, 9, 4, 0, 0, 0, TimeSpan.Zero);
        var assignment = new Assignment(
            $"asg-{epoch}-{attempt}", "run-1", "stg-1", execution, attempt, Worker, "agent.dev",
            $"lease-{epoch}-{attempt}", epoch, new[] { "development" },
            issued, expiresAt ?? issued + TimeSpan.FromMinutes(10), "policy-rev-1");

        return new CommandEnvelope
        {
            MessageId = messageId,
            SchemaVersion = "command.v1",
            MessageType = MessageTypes.ExecutionAssign,
            CorrelationId = "run-1",
            IdempotencyKey = idempotency,
            WorkflowRunId = "run-1",
            StageRunId = "stg-1",
            ExecutionId = execution,
            AttemptId = attempt,
            AssignmentId = assignment.AssignmentId,
            WorkerId = Worker,
            AgentId = "agent.dev",
            LeaseId = assignment.LeaseId,
            LeaseEpoch = epoch,
            IssuedAt = issued,
            ExpiresAt = assignment.ExpiresAt,
            Traceparent = Trace,
            Payload = System.Text.Json.JsonSerializer.Serialize(assignment),
            PolicyRevisionId = "policy-rev-1",
        };
    }

    // ---------------- journal ----------------

    [Fact]
    public void Journal_dedup_survives_a_full_restart()
    {
        var command = Assign("cmd-1", "idem-1");

        using (var first = new SqliteNodeCommandJournal(_dbPath))
        {
            Assert.Equal(JournalAttempt.Accepted, first.TryAccept(command, "msg:cmd-1", "idem:idem-1"));
        }

        // A brand-new process against the same file must see the redelivery as
        // duplicate, not accept-and-lose-or-double it.
        using var reopened = new SqliteNodeCommandJournal(_dbPath);
        Assert.Equal(JournalAttempt.Duplicate, reopened.TryAccept(command, "msg:cmd-1", "idem:idem-1"));
        Assert.Single(reopened.All());
    }

    [Fact]
    public void Pending_command_survives_restart_until_local_execution_is_durably_complete()
    {
        var command = Assign("cmd-pending", "idem-pending");
        using (var first = new SqliteNodeCommandJournal(_dbPath))
        {
            Assert.Equal(JournalAttempt.Accepted,
                first.TryAccept(command, "msg:cmd-pending", "idem:idem-pending"));
            Assert.Single(first.Pending());
        }

        using (var restarted = new SqliteNodeCommandJournal(_dbPath))
        {
            Assert.Equal("cmd-pending", Assert.Single(restarted.Pending()).MessageId);
            restarted.MarkCompleted("cmd-pending");
        }

        using var completed = new SqliteNodeCommandJournal(_dbPath);
        Assert.Empty(completed.Pending());
        Assert.Equal(JournalAttempt.Duplicate,
            completed.TryAccept(command, "msg:cmd-pending", "idem:idem-pending"));
    }

    [Fact]
    public void Business_key_collision_is_detected_without_a_partial_write()
    {
        using var journal = new SqliteNodeCommandJournal(_dbPath);
        Assert.Equal(JournalAttempt.Accepted,
            journal.TryAccept(Assign("cmd-a", "shared"), "msg:cmd-a", "idem:shared"));

        // Different message, same business operation: duplicate, and the row
        // count must prove nothing half-written landed.
        Assert.Equal(JournalAttempt.Duplicate,
            journal.TryAccept(Assign("cmd-b", "shared", attempt: "att-2"), "msg:cmd-b", "idem:shared"));

        // All() groups by message id; the table holds exactly the two rows
        // (keys) of the first command.
        Assert.Single(journal.All());
    }

    [Fact]
    public void Receiver_rebuilds_assignment_state_from_the_persisted_journal()
    {
        using var journal = new SqliteNodeCommandJournal(_dbPath);
        var tracker = new AssignmentTracker();
        var receiver = new NodeCommandReceiver(Worker, journal, tracker, () => _now);

        Assert.Equal(AcceptanceKind.Accepted,
            receiver.TryAccept(Assign("cmd-live", "idem-live", execution: "exec-live", attempt: "att-l")).Kind);
        Assert.Equal(AcceptanceKind.Accepted,
            receiver.TryAccept(Assign("cmd-dead", "idem-dead", execution: "exec-dead", attempt: "att-d",
                expiresAt: _now + TimeSpan.FromMinutes(5))).Kind);

        // Restart six minutes later: the live lease continues, the expired one
        // is explicitly released (A2 exit: continue or release, never zombie).
        _now = _now.AddMinutes(6);
        var tracker2 = new AssignmentTracker();
        var receiver2 = new NodeCommandReceiver(Worker, journal, tracker2, () => _now);
        var released = receiver2.RebuildAfterRestart();

        Assert.Single(released);
        Assert.Equal("exec-dead", released[0].ExecutionId);
        Assert.True(tracker2.MaySubmitResult("asg-1-att-l", _now));
        Assert.Null(tracker2.CurrentFor("exec-dead"));
    }

    // ---------------- event store ----------------

    [Fact]
    public void Events_persist_redacted_and_dedup_across_restarts()
    {
        using var sink1 = new SqliteEventSink(_dbPath);
        var store1 = new LocalEventStore(sink: sink1);
        var envelope = LocalEvents.For(Worker, "att-1", "agentboard.execution.stdout", "run-1",
            "token sk-supersecretvalue1234567890 printed", _now);

        Assert.Equal(EventAppendKind.Stored, store1.TryAppend(envelope, out var stored, out _));
        Assert.Contains("[REDACTED", stored.Data);
        using var sink2 = new SqliteEventSink(_dbPath);
        var store2 = new LocalEventStore(sink: sink2);

        // Same event id after restart: still duplicate, and the persisted row
        // is the redacted one — the secret never reaches the file.
        Assert.Equal(EventAppendKind.Duplicate, store2.TryAppend(envelope, out var replayed, out _));
        Assert.DoesNotContain("sk-supersecretvalue1234567890", replayed.Data);
        Assert.Single(store2.ForAttempt("att-1"));
    }

    // ---------------- result outbox ----------------

    [Fact]
    public void Unconfirmed_result_is_re_adopted_after_restart_and_retries_under_same_keys()
    {
        var failing = new FakeTransport(failTimes: 1);
        var outbox1 = new LocalResultOutbox(failing, () => _now, log: new SqliteResultOutboxLog(_dbPath));
        var result = MakeResult();

        outbox1.Enqueue(result);
        Assert.Equal(0, outbox1.Drain()); // first publish fails; record stays pending

        // Restart: the new outbox re-adopts the owed record from the log and
        // publishes it under the ORIGINAL message and idempotency keys, once
        // the recorded backoff has elapsed (it survives the restart too).
        _now = _now.AddMinutes(1);
        var transport2 = new FakeTransport();
        var outbox2 = new LocalResultOutbox(transport2, () => _now, log: new SqliteResultOutboxLog(_dbPath));
        var owed = Assert.Single(outbox2.UnackedAfterRestart());
        Assert.Equal(result.MessageId, owed.Result.MessageId);
        Assert.Equal(1, outbox2.Drain());

        // And a third "restart" sees it confirmed, so it never republishes.
        var outbox3 = new LocalResultOutbox(new FakeTransport(), () => _now, log: new SqliteResultOutboxLog(_dbPath));
        Assert.Empty(outbox3.UnackedAfterRestart());
        Assert.Equal(LocalOutboxState.Confirmed, Assert.Single(outbox3.Records).State);
    }

    [Fact]
    public void Transport_exception_is_scheduled_for_retry_instead_of_stranding_published()
    {
        var outbox = new LocalResultOutbox(
            new ThrowingTransport(), () => _now,
            baseDelay: TimeSpan.FromSeconds(2),
            log: new SqliteResultOutboxLog(_dbPath));

        outbox.Enqueue(MakeResult());
        Assert.Equal(0, outbox.Drain());

        var pending = Assert.Single(outbox.Records);
        Assert.Equal(LocalOutboxState.Pending, pending.State);
        Assert.Equal(_now.AddSeconds(2), pending.NextAttemptAt);
        Assert.Equal("publish threw IOException", pending.LastError);
    }

    [Fact]
    public void Crash_after_marking_published_is_retried_after_restart()
    {
        var result = MakeResult();
        using (var log = new SqliteResultOutboxLog(_dbPath))
        {
            log.Save(new LocalOutboxRecord(
                result.MessageId, result.IdempotencyKey, result,
                LocalOutboxState.Published, 1, _now, null, null, null));
        }

        var transport = new FakeTransport();
        var restarted = new LocalResultOutbox(
            transport, () => _now, log: new SqliteResultOutboxLog(_dbPath));

        Assert.Equal(1, restarted.Drain());
        Assert.Equal(1, transport.PublishCount);
        Assert.Equal(LocalOutboxState.Confirmed, Assert.Single(restarted.Records).State);
    }

    [Fact]
    public void Approval_grant_survives_restart_and_remains_bound_to_its_exact_context()
    {
        var grant = new ApprovalGrant(
            "apr-1", PolicyActionKinds.GitCommit, "/repo/a.cs", "agent.dev",
            StageType.Development, "run-1", "project-1", "workspace-1", "commit-1",
            "policy-rev-1", "operator", _now.AddMinutes(5));
        using (var store = new SqliteApprovalGrantStore(_dbPath))
        {
            new LocalApprovalLedger(store).Record(grant);
        }

        using var reopened = new SqliteApprovalGrantStore(_dbPath);
        var ledger = new LocalApprovalLedger(reopened);
        var request = new PolicyDecisionRequest(
            new PolicyAction(PolicyActionKinds.GitCommit, "/repo/a.cs"),
            "agent.dev", new[] { "development" }, StageType.Development, "run-1",
            new WorkspaceReference("project-1", "workspace-1", "commit-1"),
            "policy-rev-1", ApprovalGranted: true, ApprovalId: "apr-1");

        Assert.True(ledger.IsGranted("apr-1", request, _now));
        Assert.False(ledger.IsGranted("apr-1", request with
        {
            Workspace = new WorkspaceReference("project-1", "workspace-1", "commit-2"),
        }, _now));
    }

    private ResultEnvelope MakeResult() => new()
    {
        MessageId = "msg-r1",
        SchemaVersion = "result.v1",
        MessageType = MessageTypes.ExecutionResult,
        CorrelationId = "run-1",
        IdempotencyKey = "asg-1-att-1:att-1",
        WorkflowRunId = "run-1",
        StageRunId = "stg-1",
        ExecutionId = "exec-1",
        AttemptId = "att-1",
        AssignmentId = "asg-1-att-1",
        WorkerId = Worker,
        AgentId = "agent.dev",
        LeaseEpoch = 1,
        ResultStatus = AttemptResultStatus.Succeeded,
        OutcomeSummary = "done",
        Traceparent = Trace,
        CreatedAt = _now,
    };

    private sealed class FakeTransport : IResultTransport
    {
        private int _remainingFailures;

        public FakeTransport(int failTimes = 0) => _remainingFailures = failTimes;

        public int PublishCount;

        public BrokerConfirm Publish(LocalOutboxRecord record)
        {
            PublishCount++;
            if (_remainingFailures > 0)
            {
                _remainingFailures--;
                return BrokerConfirm.Failed;
            }

            return BrokerConfirm.Confirmed;
        }
    }

    private sealed class ThrowingTransport : IResultTransport
    {
        public BrokerConfirm Publish(LocalOutboxRecord record) =>
            throw new IOException("amqp://user:secret@broker unavailable");
    }
}
