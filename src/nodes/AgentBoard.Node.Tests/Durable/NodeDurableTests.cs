// SPDX-License-Identifier: MIT
using AgentBoard.Contracts;
using AgentBoard.Node.Durable;
using Xunit;

namespace AgentBoard.Node.Tests.Durable;

/// <summary>
/// A2 exit criteria for the Node execution plane: durable accept before ACK
/// (doc 151 §5.5, §6.1), local detail + result outbox (§6.2), and the local
/// policy PDP/PEP with default-deny and fail-fast approvals (doc 150 PR-005).
/// </summary>
public class NodeDurableTests
{
    private const string Worker = "worker-7";
    private const string Trace = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01";

    // Instance clock: xunit may run classes in parallel, and mutating
    // a shared static would leak time between unrelated tests.
    private DateTimeOffset _now = new(2026, 9, 4, 0, 0, 0, TimeSpan.Zero);

    private CommandEnvelope Assign(
        string execution = "exec-1",
        string attempt = "att-1",
        long epoch = 1,
        string worker = Worker,
        string? messageId = null,
        string? idempotency = null,
        DateTimeOffset? expiresAt = null)
    {
        var issued = _now;
        var assignment = new Assignment(
            $"asg-{epoch}-{attempt}", "run-1", "stg-1", execution, attempt, worker, "agent.dev",
            $"lease-{epoch}-{attempt}", epoch, new[] { "development" },
            issued, expiresAt ?? issued + TimeSpan.FromMinutes(10), "policy-rev-1");

        return new CommandEnvelope
        {
            MessageId = messageId ?? $"cmd-{execution}-{epoch}",
            SchemaVersion = "command.v1",
            MessageType = MessageTypes.ExecutionAssign,
            CorrelationId = "run-1",
            IdempotencyKey = idempotency ?? $"{assignment.AssignmentId}:{attempt}",
            WorkflowRunId = "run-1",
            StageRunId = "stg-1",
            ExecutionId = execution,
            AttemptId = attempt,
            AssignmentId = assignment.AssignmentId,
            WorkerId = worker,
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

    private ResultEnvelope Result(Assignment assignment, string summary = "done") => new()
    {
        MessageId = $"msg-{assignment.AttemptId}",
        SchemaVersion = "result.v1",
        MessageType = MessageTypes.ExecutionResult,
        CorrelationId = "run-1",
        IdempotencyKey = $"{assignment.AssignmentId}:result",
        WorkflowRunId = assignment.WorkflowRunId,
        StageRunId = assignment.StageRunId,
        ExecutionId = assignment.ExecutionId,
        AttemptId = assignment.AttemptId,
        AssignmentId = assignment.AssignmentId,
        WorkerId = assignment.WorkerId,
        AgentId = assignment.AgentId,
        LeaseEpoch = assignment.LeaseEpoch,
        ResultStatus = AttemptResultStatus.Succeeded,
        OutcomeSummary = summary,
        Traceparent = Trace,
        CreatedAt = _now,
    };

    // ------------------------------------------------------------------
    // Inbox: durable accept before ACK
    // ------------------------------------------------------------------

    [Fact]
    public void Accepted_command_learns_the_assignment_and_may_submit_results()
    {
        var tracker = new AssignmentTracker();
        var receiver = new NodeCommandReceiver(Worker, new InMemoryNodeCommandJournal(), tracker, () => _now);

        var acceptance = receiver.TryAccept(Assign());

        Assert.Equal(AcceptanceKind.Accepted, acceptance.Kind);
        Assert.True(acceptance.ShouldAckBroker);

        var assignment = tracker.CurrentFor("exec-1")!;
        Assert.NotNull(assignment);
        Assert.True(tracker.MaySubmitResult(assignment.AssignmentId, _now));
    }

    [Fact]
    public void Duplicate_is_acked_but_never_reapplied()
    {
        var journal = new InMemoryNodeCommandJournal();
        var tracker = new AssignmentTracker();
        var receiver = new NodeCommandReceiver(Worker, journal, tracker, () => _now);

        receiver.TryAccept(Assign());
        var duplicate = receiver.TryAccept(Assign());

        Assert.Equal(AcceptanceKind.Duplicate, duplicate.Kind);
        Assert.True(duplicate.ShouldAckBroker);

        // The journal holds the message key and the business key, once each.
        Assert.Equal(2, journal.All().Count);
    }

    [Fact]
    public void Malformed_and_misaddressed_commands_never_reach_the_ack_path()
    {
        var receiver = new NodeCommandReceiver(
            Worker, new InMemoryNodeCommandJournal(), new AssignmentTracker(), () => _now);

        var malformed = Assign() with { SchemaVersion = string.Empty };
        var schema = receiver.TryAccept(malformed);
        Assert.Equal(AcceptanceKind.RejectedSchema, schema.Kind);
        Assert.False(schema.ShouldAckBroker);

        var misaddressed = receiver.TryAccept(Assign(worker: "someone-else"));
        Assert.Equal(AcceptanceKind.RejectedNotForThisWorker, misaddressed.Kind);
        Assert.False(misaddressed.ShouldAckBroker);
    }

    [Fact]
    public void Journal_failure_means_no_ack_and_no_local_state()
    {
        var tracker = new AssignmentTracker();
        var receiver = new NodeCommandReceiver(Worker, new ThrowingJournal(), tracker, () => _now);

        // The broker must redeliver: the append failed, so the exception
        // surfaces to the consumer and no assignment is recorded.
        Assert.Throws<IOException>(() => receiver.TryAccept(Assign()));
        Assert.Null(tracker.CurrentFor("exec-1"));
    }

    [Fact]
    public void Superseding_epoch_replaces_local_right_to_submit()
    {
        var tracker = new AssignmentTracker();
        var receiver = new NodeCommandReceiver(Worker, new InMemoryNodeCommandJournal(), tracker, () => _now);

        var first = receiver.TryAccept(Assign());
        var firstAssignment = AssignmentTracker.ParseAssignment(first.Command!);

        Assert.Equal(AcceptanceKind.Accepted, receiver.TryAccept(Assign(attempt: "att-2", epoch: 2)).Kind);

        Assert.False(tracker.MaySubmitResult(firstAssignment.AssignmentId, _now));
        Assert.True(tracker.MaySubmitResult($"asg-2-att-2", _now));
    }

    [Fact]
    public void Restart_continues_live_assignments_and_releases_expired_ones()
    {
        var journal = new InMemoryNodeCommandJournal();
        var tracker = new AssignmentTracker();
        var receiver = new NodeCommandReceiver(Worker, journal, tracker, () => _now);

        receiver.TryAccept(Assign(execution: "exec-live", attempt: "att-l"));
        receiver.TryAccept(Assign(execution: "exec-dead", attempt: "att-d",
            expiresAt: _now + TimeSpan.FromMinutes(5)));

        // "Process restarts" five minutes later.
        _now = _now.AddMinutes(6);
        var receiver2 = new NodeCommandReceiver(Worker, journal, tracker = new AssignmentTracker(), () => _now);
        var released = receiver2.RebuildAfterRestart();

        Assert.Single(released);
        Assert.Equal("exec-dead", released[0].ExecutionId);
        Assert.True(tracker.MaySubmitResult("asg-1-att-l", _now));
        Assert.Null(tracker.CurrentFor("exec-dead"));
    }

    // ------------------------------------------------------------------
    // Local event store + result outbox
    // ------------------------------------------------------------------

    [Fact]
    public void Events_dedup_on_source_plus_id_and_reject_malformed_sources()
    {
        var store = new LocalEventStore();
        var envelope = LocalEvents.For(Worker, "att-1", "agentboard.execution.tool_call", "run-1", "ls", _now);

        Assert.Equal(EventAppendKind.Stored, store.TryAppend(envelope, out var stored, out _));
        Assert.Equal(EventAppendKind.Duplicate, store.TryAppend(envelope, out _, out _));

        var bad = envelope with { EventId = "evt-other", Source = "host/att-1" };
        Assert.Equal(EventAppendKind.RejectedSchema, store.TryAppend(bad, out _, out _));
        Assert.Single(store.ForAttempt("att-1"));
    }

    [Fact]
    public void Secrets_are_redacted_before_the_event_is_stored()
    {
        var store = new LocalEventStore();
        var envelope = LocalEvents.For(Worker, "att-1", "agentboard.execution.stdout", "run-1",
            "export API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456 && echo done", _now);

        Assert.Equal(EventAppendKind.Stored, store.TryAppend(envelope, out var stored, out _));
        Assert.DoesNotContain("sk-abcdefghijklmnopqrstuvwxyz123456", stored.Data);
        Assert.Contains("[REDACTED", stored.Data);
    }

    [Fact]
    public void Result_is_durable_locally_before_first_publish_and_replays_after_restart()
    {
        var transport = new FakeResultTransport();
        var outbox = new LocalResultOutbox(transport, () => _now);
        var assignment = AssignmentTracker.ParseAssignment(Assign());
        var result = Result(assignment);

        outbox.Enqueue(result);
        Assert.Equal(1, outbox.Drain());
        Assert.Equal(0, outbox.Drain());
        Assert.Equal(1, transport.PublishCount);

        Assert.Empty(outbox.UnackedAfterRestart()); // nothing owed: it is confirmed

        outbox.Enqueue(result); // same message id: no duplicate record
        Assert.Equal(1, outbox.Records.Count);
    }

    [Fact]
    public void Unconfirmed_publishes_back_off_then_quarantine()
    {
        var transport = new FakeResultTransport(alwaysFail: true);
        var outbox = new LocalResultOutbox(transport, () => _now, maxAttempts: 2,
            baseDelay: TimeSpan.FromSeconds(10), maxDelay: TimeSpan.FromSeconds(30));
        var assignment = AssignmentTracker.ParseAssignment(Assign());
        outbox.Enqueue(Result(assignment));

        outbox.Drain();
        Assert.Equal(LocalOutboxState.Pending, outbox.Records.Single().State);

        _now = _now.AddMinutes(1);
        outbox.Drain();
        Assert.Equal(LocalOutboxState.DeadLettered, outbox.Records.Single().State);
        Assert.Equal(2, transport.PublishCount);
    }

    // ------------------------------------------------------------------
    // Policy: presets, default deny, approvals, enforcement
    // ------------------------------------------------------------------

    private static PolicyDecisionRequest Request(
        CompiledPolicy revision,
        string kind,
        string resource = "/src/a.cs",
        bool approvalGranted = false,
        string agent = "agent.dev") => new(
        new PolicyAction(kind, resource), agent, new[] { "development" },
        StageType.Development, "run-1",
        new WorkspaceReference("proj-1", "ws-1", "commit-sha"),
        revision.RevisionId, approvalGranted);

    [Fact]
    public void Presets_compile_to_deterministic_revisions_with_distinct_id()
    {
        var review = CompiledPolicy.Compile(PolicyPresets.Review, new Dictionary<string, PolicyDecision>());
        var reviewAgain = CompiledPolicy.Compile(PolicyPresets.Review, new Dictionary<string, PolicyDecision>());
        Assert.Equal(review.RevisionId, reviewAgain.RevisionId);

        var dev = CompiledPolicy.Compile(PolicyPresets.Developer, new Dictionary<string, PolicyDecision>());
        Assert.NotEqual(review.RevisionId, dev.RevisionId);

        Assert.Throws<ArgumentException>(() =>
            CompiledPolicy.Compile("yolo", new Dictionary<string, PolicyDecision>()));
        Assert.Throws<ArgumentException>(() =>
            CompiledPolicy.Compile(PolicyPresets.Review,
                new Dictionary<string, PolicyDecision> { ["launch_missiles"] = PolicyDecision.Allow }));
    }

    [Fact]
    public void Review_preset_denies_writes_and_unknown_actions_default_deny()
    {
        var revision = CompiledPolicy.Compile(PolicyPresets.Review, new Dictionary<string, PolicyDecision>());
        var pdp = new PolicyDecisionPoint(revision);

        Assert.Equal(PolicyDecision.Deny, pdp.Decide(Request(revision, PolicyActionKinds.WriteFile)).Decision);

        // Unknown kinds deny via DefaultDenyForUnknownKind — but the validator
        // only sees the kind in the request, so craft one that survives field
        // checks: an unrecognized kind still denies.
        var (decision, failure) = pdp.Decide(Request(revision, "not_a_known_kind"));
        Assert.Equal(PolicyDecision.Deny, decision);
        Assert.Equal(FailureCategory.PolicyDenied, failure);
    }

    [Fact]
    public void Unattended_require_approval_fails_fast_never_hangs()
    {
        var revision = CompiledPolicy.Compile(PolicyPresets.Developer, new Dictionary<string, PolicyDecision>());
        var pdp = new PolicyDecisionPoint(revision);

        var (decision, failure) = pdp.Decide(Request(revision, PolicyActionKinds.GitCommit));

        Assert.Equal(PolicyDecision.Deny, decision);
        Assert.Equal(FailureCategory.ApprovalUnavailable, failure);
    }

    [Fact]
    public void Granted_approval_lets_the_enforcement_point_run_the_action()
    {
        var revision = CompiledPolicy.Compile(PolicyPresets.Developer, new Dictionary<string, PolicyDecision>());
        var audit = new List<PolicyEnforcementPoint.AuditLine>();
        var pep = new PolicyEnforcementPoint(new PolicyDecisionPoint(revision), audit.Add);

        var executed = pep.Execute(Request(revision, PolicyActionKinds.GitCommit, approvalGranted: true),
            () => "committed");

        Assert.Equal(EnforcementOutcome.Executed, executed.Outcome);
        Assert.Equal("committed", executed.Value);
        Assert.Single(audit);
    }

    [Fact]
    public void Denied_action_is_never_invoked_and_is_audited()
    {
        var revision = CompiledPolicy.Compile(PolicyPresets.Review, new Dictionary<string, PolicyDecision>());
        var audit = new List<PolicyEnforcementPoint.AuditLine>();
        var pep = new PolicyEnforcementPoint(new PolicyDecisionPoint(revision), audit.Add);

        var ran = false;
        var result = pep.Execute(Request(revision, PolicyActionKinds.WriteFile), () => { ran = true; return 1; });

        Assert.False(ran);
        Assert.Equal(EnforcementOutcome.Denied, result.Outcome);
        Assert.Equal(FailureCategory.PolicyDenied, result.Failure);
        Assert.Equal(PolicyDecision.Deny, audit.Single().Decision);
    }

    [Fact]
    public void Stale_policy_revision_refuses_to_evaluate()
    {
        var revision = CompiledPolicy.Compile(PolicyPresets.Developer, new Dictionary<string, PolicyDecision>());
        var pdp = new PolicyDecisionPoint(revision);

        var request = Request(revision, PolicyActionKinds.ReadFile) with { PolicyRevisionId = "policy-rev-old" };
        Assert.Equal(PolicyDecision.Deny, pdp.Decide(request).Decision);
    }

    private sealed class ThrowingJournal : INodeCommandJournal
    {
        public bool Contains(string dedupKey) => false;

        public void Append(CommandEnvelope command, string dedupKey) =>
            throw new IOException("simulated store crash before durable accept");

        public IReadOnlyList<CommandEnvelope> All() => Array.Empty<CommandEnvelope>();
    }

    private sealed class FakeResultTransport : IResultTransport
    {
        private readonly bool _alwaysFail;

        public FakeResultTransport(bool alwaysFail = false) => _alwaysFail = alwaysFail;

        public int PublishCount;

        public BrokerConfirm Publish(LocalOutboxRecord record)
        {
            PublishCount++;
            return _alwaysFail ? BrokerConfirm.Failed : BrokerConfirm.Confirmed;
        }
    }
}
