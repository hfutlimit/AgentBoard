// SPDX-License-Identifier: MIT
using AgentBoard.Contracts;

namespace AgentBoard.Domain.Workflow.Durable;

// ---------------------------------------------------------------------------
// Durable capture/restore (doc 150 PR-002, NFR-001, NFR-005). The A1 registry
// is kept in memory while running; these DTOs are what the Infrastructure
// store persists, and the Restore paths rebuild the machines through their
// resume constructors — never by faking transitions, so a restarted Server
// continues from exactly the persisted state.
//
// Persistence granularity is deliberately per-component snapshots rather than
// per-entity rows; the transaction boundary that matters for the A1 exit
// criteria (no "confirmed but unrecorded" window) is enforced by the store
// wrapping "registry state + outbox rows" in one commit. Row-level journaling
// is a scale concern for A4, not a semantics concern here.
// ---------------------------------------------------------------------------

public sealed record RunState(WorkflowRun Run, string VersionId);

public sealed record AttemptState(ExecutionAttempt Attempt, AttemptResult? Result);

public sealed record OutcomeState(string ExecutionId, Outcome Outcome);

public sealed record RegistryState(
    IReadOnlyList<WorkflowVersion> Versions,
    IReadOnlyList<RunState> Runs,
    IReadOnlyList<StageRun> Stages,
    IReadOnlyList<Execution> Executions,
    IReadOnlyList<AttemptState> Attempts,
    IReadOnlyList<OutcomeState> Outcomes,
    IReadOnlyList<AuditRecord> Audit);

public sealed record PlaneState(
    RegistryState Registry,
    IReadOnlyList<OutboxMessage> Outbox,
    IReadOnlyList<DedupEntry> Inbox,
    IReadOnlyList<Assignment> Assignments,
    IReadOnlyList<DeadLetterEntry> DeadLetters,
    IReadOnlyList<ApprovalRequest> Approvals,
    IReadOnlyList<CommandEnvelope> SentCommands,
    IReadOnlyList<PendingRetry> PendingRetries,
    IReadOnlyList<HandoffContext> Handoffs,
    IReadOnlyList<AttemptEvidence> Evidence,
    WorkflowOrchestrationState Orchestration,
    IReadOnlyList<TaskStatusProjection>? TaskStatusProjections = null);

/// <summary>
/// What an accepted result contributed for later stages: the bounded evidence
/// fields of the result envelope (artifacts, commit, test evidence, review
/// findings). Previously these were dropped at intake, which made a truthful
/// HandoffContext impossible to build — handoffs were specified (doc 150
/// PR-010) but had no production producer (doc 151 §7).
/// </summary>
public sealed record AttemptEvidence(
    string AttemptId,
    string OutcomeSummary,
    IReadOnlyList<ArtifactReference> ArtifactReferences,
    string? CommitOrVersion,
    IReadOnlyList<string> TestEvidence,
    IReadOnlyList<string> ReviewFindings);

/// <summary>Durable Server-side handoff registry (doc 151 §7).</summary>
public sealed class HandoffRegistry
{
    private readonly Dictionary<string, HandoffContext> _byId = new(StringComparer.Ordinal);

    public IReadOnlyCollection<HandoffContext> Handoffs => _byId.Values;

    public HandoffContext Add(HandoffContext handoff)
    {
        var frozen = Freeze(handoff);
        var errors = EnvelopeValidator.Validate(frozen);
        if (errors.Count > 0)
        {
            throw new Common.InvalidValueException(
                $"invalid handoff: {string.Join("; ", errors.Select(e => $"{e.Field} {e.Reason}"))}");
        }

        if (!_byId.TryAdd(frozen.HandoffId, frozen))
        {
            throw new Common.DuplicateException($"handoff '{handoff.HandoffId}' already exists");
        }

        return frozen;
    }

    public HandoffContext Require(string handoffId) =>
        _byId.TryGetValue(handoffId, out var handoff)
            ? handoff
            : throw new Common.NotFoundException($"handoff '{handoffId}' not found");

    public bool TryGet(string handoffId, out HandoffContext handoff) => _byId.TryGetValue(handoffId, out handoff!);

    internal void Clear() => _byId.Clear();

    internal void Restore(IReadOnlyList<HandoffContext> handoffs)
    {
        foreach (var handoff in handoffs)
        {
            var frozen = Freeze(handoff);
            var errors = EnvelopeValidator.Validate(frozen);
            if (errors.Count > 0)
            {
                throw new Common.InvalidValueException(
                    $"persisted handoff '{handoff.HandoffId}' is invalid: " +
                    string.Join("; ", errors.Select(e => $"{e.Field} {e.Reason}")));
            }
            _byId[frozen.HandoffId] = frozen;
        }
    }

    public IReadOnlyList<HandoffContext> Capture() => _byId.Values.ToList();

    private static HandoffContext Freeze(HandoffContext handoff) => handoff with
    {
        ArtifactReferences = new System.Collections.ObjectModel.ReadOnlyCollection<ArtifactReference>(
            handoff.ArtifactReferences.ToArray()),
        TestEvidence = new System.Collections.ObjectModel.ReadOnlyCollection<string>(
            handoff.TestEvidence.ToArray()),
        ReviewFindings = new System.Collections.ObjectModel.ReadOnlyCollection<string>(
            handoff.ReviewFindings.ToArray()),
        RequiredCapabilities = new System.Collections.ObjectModel.ReadOnlyCollection<string>(
            handoff.RequiredCapabilities.ToArray()),
    };
}

/// <summary>Accepted results' evidence, keyed by attempt (bounded fields only).</summary>
public sealed class AttemptEvidenceLog
{
    private readonly Dictionary<string, AttemptEvidence> _byAttempt = new(StringComparer.Ordinal);

    public IReadOnlyCollection<AttemptEvidence> Entries => _byAttempt.Values;

    public void Record(ResultEnvelope result) => _byAttempt[result.AttemptId] = new AttemptEvidence(
        result.AttemptId,
        result.OutcomeSummary ?? string.Empty,
        new System.Collections.ObjectModel.ReadOnlyCollection<ArtifactReference>(result.ArtifactReferences.ToArray()),
        result.CommitOrVersion,
        new System.Collections.ObjectModel.ReadOnlyCollection<string>(result.TestEvidence.ToArray()),
        new System.Collections.ObjectModel.ReadOnlyCollection<string>(result.ReviewFindings.ToArray()));

    public AttemptEvidence? For(string attemptId) =>
        _byAttempt.TryGetValue(attemptId, out var evidence) ? evidence : null;

    internal void Clear() => _byAttempt.Clear();

    internal void Restore(IReadOnlyList<AttemptEvidence> entries)
    {
        foreach (var entry in entries)
        {
            _byAttempt[entry.AttemptId] = entry;
        }
    }

    public IReadOnlyList<AttemptEvidence> Capture() => _byAttempt.Values.ToList();
}

public static class RegistryPersistence
{
    public static RegistryState CaptureState(this WorkflowRegistry registry) => new(
        registry.Versions.ToList(),
        registry.Runs.Select(r => new RunState(r.Current, r.VersionId)).ToList(),
        registry.Runs.SelectMany(r => r.Stages).Select(s => s.Current).ToList(),
        registry.Runs.SelectMany(r => r.Stages).SelectMany(s => s.Executions).Select(e => e.Current).ToList(),
        registry.Runs.SelectMany(r => r.Stages).SelectMany(s => s.Executions)
            .SelectMany(e => e.Attempts).Select(a => new AttemptState(a.Current, a.Result)).ToList(),
        registry.Runs.SelectMany(r => r.Stages).SelectMany(s => s.Executions)
            .Where(e => e.Outcome is not null)
            .Select(e => new OutcomeState(e.Current.ExecutionId, e.Outcome!)).ToList(),
        registry.Audit.Records.ToList());

    /// <summary>
    /// Rebuilds a registry from persisted state into a fresh instance. Machines
    /// resume from the stored states (empty in-process history by design); the
    /// audit trail carries the full sequence, so recovery remains queryable.
    /// </summary>
    public static WorkflowRegistry RestoreRegistry(this Func<DateTimeOffset> clock, RegistryState state)
    {
        var registry = new WorkflowRegistry(clock);
        registry.Load(state);
        return registry;
    }
}

public sealed partial class WorkflowRegistry
{
    /// <summary>Loads persisted state into this (fresh) registry instance.</summary>
    internal void Load(RegistryState state)
    {
        // Restored copies arrive as mutable lists from JSON; freeze them again
        // or a restart silently un-freezes released graphs.
        foreach (var version in state.Versions)
        {
            _versions[version.VersionId] = WorkflowGraph.Freeze(version);
        }

        foreach (var run in state.Runs)
        {
            _runs[run.Run.RunId] = new TrackedRun(run.Run, run.VersionId);
        }

        foreach (var stage in state.Stages)
        {
            var tracked = new TrackedStage(stage);
            _stages[stage.StageRunId] = tracked;
            if (_runs.TryGetValue(stage.RunId, out var run))
            {
                run.Add(tracked);
            }
        }

        foreach (var execution in state.Executions)
        {
            var tracked = new TrackedExecution(execution);
            _executions[execution.ExecutionId] = tracked;
            if (_stages.TryGetValue(execution.StageRunId, out var stage))
            {
                stage.Add(tracked);
            }
        }

        foreach (var attempt in state.Attempts)
        {
            var tracked = new TrackedAttempt(attempt.Attempt) { Result = attempt.Result };
            _attempts[attempt.Attempt.AttemptId] = tracked;
            if (_executions.TryGetValue(attempt.Attempt.ExecutionId, out var execution))
            {
                execution.Add(tracked);
            }
        }

        foreach (var outcome in state.Outcomes)
        {
            if (_executions.TryGetValue(outcome.ExecutionId, out var execution))
            {
                execution.Outcome = outcome.Outcome;
            }
        }

        Audit.Restore(state.Audit);
    }
}

public sealed partial class AuditTrail
{
    internal void Clear() => _records.Clear();

    internal void Restore(IReadOnlyList<AuditRecord> records)
    {
        foreach (var record in records)
        {
            if (record.Sequence <= _records.Count)
            {
                continue; // already present (double-restore guard)
            }

            _records.Add(record);
        }
    }
}

public sealed partial class ServerOutbox
{
    internal void Clear() => _messages.Clear();

    public IReadOnlyList<OutboxMessage> Capture() => _messages.Values.ToList();

    internal void Restore(IReadOnlyList<OutboxMessage> messages)
    {
        foreach (var message in messages)
        {
            _messages[message.MessageId] = message;
        }
    }
}

public sealed partial class Inbox
{
    internal void Clear() => _entries.Clear();

    public IReadOnlyList<DedupEntry> Capture() => _entries.Values.ToList();

    internal void Restore(IReadOnlyList<DedupEntry> entries)
    {
        foreach (var entry in entries)
        {
            _entries[entry.Key] = entry;
        }
    }
}

public sealed partial class LeaseRegistry
{
    internal void Clear()
    {
        _byId.Clear();
        _currentEpoch.Clear();
    }

    public IReadOnlyList<Assignment> Capture() => _byId.Values.ToList();

    internal void Restore(IReadOnlyList<Assignment> assignments)
    {
        foreach (var assignment in assignments)
        {
            _byId[assignment.AssignmentId] = assignment;
            if (assignment.LeaseEpoch > (_currentEpoch.TryGetValue(assignment.ExecutionId, out var epoch) ? epoch : 0))
            {
                _currentEpoch[assignment.ExecutionId] = assignment.LeaseEpoch;
            }
        }
    }
}

public sealed partial class DeadLetterQueue
{
    internal void Clear() => _entries.Clear();

    public IReadOnlyList<DeadLetterEntry> Capture() => _entries.Values.ToList();

    internal void Restore(IReadOnlyList<DeadLetterEntry> entries)
    {
        foreach (var entry in entries)
        {
            _entries[entry.Id] = entry;
        }
    }
}

public sealed partial class ApprovalInbox
{
    internal void Clear() => _requests.Clear();

    public IReadOnlyList<ApprovalRequest> Capture() => _requests.Values.ToList();

    internal void Restore(IReadOnlyList<ApprovalRequest> requests)
    {
        foreach (var request in requests)
        {
            _requests[request.ApprovalId] = request;
        }
    }
}

/// <summary>
/// Commit target for the plane's durable snapshots. Implemented by the
/// Infrastructure store; kept as an interface so the Domain can run its own
/// rollback semantics in tests without a database (doc 150 NFR-001).
/// </summary>
public interface IPlaneCommitter
{
    void Commit(PlaneState state);
}

/// <summary>
/// The Server-side durable plane: registry, leases, inbox, outbox, DLQ and
/// approvals composed as one authoritative unit, with capture/restore for the
/// store to persist in a single commit (doc 151 §5.6: the state transition and
/// follow-up command issuance commit together).
/// </summary>
public sealed class DurableServerPlane
{
    public DurableServerPlane(
        Func<DateTimeOffset> clock,
        Func<string> nextId,
        RetryPlanner? planner = null,
        IAgentSelector? agentSelector = null)
    {
        Clock = clock;
        NextId = nextId;
        Registry = new WorkflowRegistry(clock);
        Leases = new LeaseRegistry(clock);
        Inbox = new Inbox(clock);
        Outbox = new ServerOutbox(clock);
        DeadLetters = new DeadLetterQueue();
        Approvals = new ApprovalInbox(Registry.Audit, clock);
        Sent = new SentCommandLog();
        Retries = new PendingRetryQueue(clock);
        TaskProjections = new TaskStatusProjectionOutbox(clock);
        Planner = planner ?? new RetryPlanner();
        Dispatcher = new CommandDispatcher(Registry, Leases, Outbox, clock, nextId, Sent, Handoffs);
        HandoffIssuer = new HandoffIssuer(Registry, Handoffs, Evidence, nextId);
        Orchestrator = new WorkflowOrchestrator(
            Registry, Orchestration, Leases, Dispatcher, HandoffIssuer,
            TaskProjections, nextId, agentSelector);
        Results = new ServerResultProcessor(
            Registry, Leases, Inbox, Planner, DeadLetters, Dispatcher, clock, nextId,
            Sent, Retries, Evidence, Orchestrator);
    }

    /// <summary>
    /// Performs <paramref name="work"/> and commits the resulting state; when
    /// the commit fails the whole in-memory plane is rolled back to the last
    /// committed snapshot, so a persistence failure can never leave the Server
    /// "advanced" against a store that knows nothing about it (doc 150
    /// NFR-001: no confirmed-but-unrecorded, and no unconfirmed-but-applied).
    /// </summary>
    public void CommitAtomic(IPlaneCommitter committer, Action work)
    {
        var prior = Capture();
        try
        {
            work();
            committer.Commit(Capture());
        }
        catch
        {
            ResetTo(prior);
            throw;
        }
    }

    /// <summary>
    /// Opens the durable approval record AND parks the stage in
    /// WaitingApproval together, so "awaiting approval" is a state the Server
    /// registry owns rather than a value a caller asserts (doc 150 PR-005).
    /// </summary>
    public ApprovalRequest AwaitApproval(
        string stageRunId, string assignmentId, string policyRevisionId, string actionKind, TimeSpan window)
    {
        var request = Approvals.Open(
            $"apr-{NextId()}", stageRunId, assignmentId, policyRevisionId, actionKind, window);

        Registry.MoveStage(stageRunId, StageRunState.WaitingApproval,
            new TransitionContext("server", $"approval requested for '{actionKind}'", SchemaVersions.Registry));
        return request;
    }

    public ApprovalRequest AwaitApproval(
        string stageRunId,
        string assignmentId,
        PolicyDecisionRequest decision,
        TimeSpan window)
    {
        var request = Approvals.Open(
            $"apr-{NextId()}", stageRunId, assignmentId, decision, window);

        Registry.MoveStage(stageRunId, StageRunState.WaitingApproval,
            new TransitionContext("server", $"approval requested for '{decision.Action.Kind}'", SchemaVersions.Registry));
        return request;
    }

    /// <summary>Resolves an open approval and moves the parked stage accordingly.</summary>
    public StageRun ResolveApproval(string approvalId, bool granted, string actor, string reason)
    {
        var request = Approvals.Decide(approvalId, granted, actor, reason);
        var approved = request.State == ApprovalState.Granted;

        var stage = Registry.MoveStage(
            request.StageRunId,
            approved ? StageRunState.Running : StageRunState.Failed,
            new TransitionContext(actor,
                request.State == ApprovalState.Expired
                    ? "approval expired"
                    : approved ? "approval granted" : "approval denied",
                SchemaVersions.Registry));
        if (!approved && Orchestrator.Manages(stage.RunId))
        {
            Orchestrator.Fail(stage.StageRunId, "approval denied or expired");
        }
        return stage;
    }

    /// <summary>Expires unattended approvals and closes their parked stages.</summary>
    public int ExpireApprovals()
    {
        var expired = Approvals.ExpireStaleRequests();
        foreach (var request in expired)
        {
            var stage = Registry.RequireStage(request.StageRunId);
            if (stage.Machine.Current == StageRunState.WaitingApproval)
            {
                Registry.MoveStage(request.StageRunId, StageRunState.Failed,
                    new TransitionContext("server", "approval window elapsed", SchemaVersions.Registry));
                if (Orchestrator.Manages(stage.Current.RunId))
                {
                    Orchestrator.Fail(stage.Current.StageRunId, "approval window elapsed");
                }
            }
        }

        return expired.Count;
    }

    /// <summary>
    /// Dispatches scheduled retries whose backoff has elapsed. Each stays in
    /// the queue until its dispatch durably succeeded: a throwing or crashing
    /// dispatch must leave the retry on record, or the deferral itself would
    /// become a way to lose work (doc 150 PR-012 queryable terminal states).
    /// </summary>
    public int ProcessDueRetries()
    {
        var dispatched = 0;
        foreach (var retry in Retries.Due())
        {
            Dispatcher.Dispatch(
                retry.ExecutionId, retry.WorkerId, retry.AgentId,
                retry.Capabilities, retry.PolicyRevisionId, retry.LeaseBudget,
                retry.HandoffId, retry.TaskContext, retry.ProviderId, retry.Workspace,
                retry.WorkItemType, retry.WorkItemId, retry.TaskType);
            Retries.Complete(retry);
            dispatched++;
        }

        return dispatched;
    }

    private void ResetTo(PlaneState prior)
    {
        Registry.Clear();
        Registry.Load(prior.Registry);
        Outbox.Clear();
        Outbox.Restore(prior.Outbox);
        Inbox.Clear();
        Inbox.Restore(prior.Inbox);
        Leases.Clear();
        Leases.Restore(prior.Assignments);
        DeadLetters.Clear();
        DeadLetters.Restore(prior.DeadLetters);
        Approvals.Clear();
        Approvals.Restore(prior.Approvals);
        Sent.Clear();
        Sent.Restore(prior.SentCommands);
        Retries.Clear();
        Retries.Restore(prior.PendingRetries);
        Handoffs.Clear();
        Handoffs.Restore(prior.Handoffs);
        Evidence.Clear();
        Evidence.Restore(prior.Evidence);
        Orchestration.Clear();
        Orchestration.Restore(prior.Orchestration);
        TaskProjections.Clear();
        TaskProjections.Restore(prior.TaskStatusProjections ?? Array.Empty<TaskStatusProjection>());
    }

    /// <summary>
    /// Issues the durable HandoffContext for the next stage from a resolved
    /// execution: source outcome, bounded evidence, workspace and required
    /// capability — the explicit, verifiable bridge that replaces "the
    /// previous provider session is still around" (doc 150 PR-010, doc 151 §7).
    /// </summary>
    public HandoffContext IssueHandoff(
        string sourceStageRunId,
        string executionId,
        StageType targetStageType,
        IReadOnlyList<string> requiredCapabilities,
        WorkspaceReference workspace,
        string taskContext = "{}")
    {
        return HandoffIssuer.Issue(
            sourceStageRunId, executionId, targetStageType,
            requiredCapabilities, workspace, taskContext);
    }

    internal Func<DateTimeOffset> Clock { get; }
    internal Func<string> NextId { get; }

    public WorkflowRegistry Registry { get; }
    public LeaseRegistry Leases { get; }
    public Inbox Inbox { get; }
    public ServerOutbox Outbox { get; }
    public DeadLetterQueue DeadLetters { get; }
    public ApprovalInbox Approvals { get; }
    public SentCommandLog Sent { get; }
    public PendingRetryQueue Retries { get; }
    public HandoffRegistry Handoffs { get; } = new();
    public AttemptEvidenceLog Evidence { get; } = new();
    public WorkflowOrchestrationRegistry Orchestration { get; } = new();
    public TaskStatusProjectionOutbox TaskProjections { get; }
    public RetryPlanner Planner { get; }
    public CommandDispatcher Dispatcher { get; }
    public HandoffIssuer HandoffIssuer { get; }
    public WorkflowOrchestrator Orchestrator { get; }
    public ServerResultProcessor Results { get; }

    public PlaneState Capture() => new(
        Registry.CaptureState(),
        Outbox.Capture(),
        Inbox.Capture(),
        Leases.Capture(),
        DeadLetters.Capture(),
        Approvals.Capture(),
        Sent.Capture(),
        Retries.Capture(),
        Handoffs.Capture(),
        Evidence.Capture(),
        Orchestration.Capture(),
        TaskProjections.Capture());

    public static DurableServerPlane Restore(
        Func<DateTimeOffset> clock,
        Func<string> nextId,
        PlaneState state,
        IAgentSelector? agentSelector = null)
    {
        var plane = new DurableServerPlane(clock, nextId, agentSelector: agentSelector);
        plane.Registry.Load(state.Registry);
        plane.Outbox.Restore(state.Outbox);
        plane.Inbox.Restore(state.Inbox);
        plane.Leases.Restore(state.Assignments);
        plane.DeadLetters.Restore(state.DeadLetters);
        plane.Approvals.Restore(state.Approvals);
        plane.Sent.Restore(state.SentCommands);
        plane.Retries.Restore(state.PendingRetries);
        plane.Handoffs.Restore(state.Handoffs);
        plane.Evidence.Restore(state.Evidence);
        plane.Orchestration.Restore(state.Orchestration);
        plane.TaskProjections.Restore(state.TaskStatusProjections ?? Array.Empty<TaskStatusProjection>());
        return plane;
    }
}

/// <summary>
/// The commands the Server issued per assignment. The result intake re-checks
/// a result against the exact envelope it caused (doc 151 §5.6: causation),
/// instead of trusting fields the Node happens to echo back.
/// </summary>
public sealed class SentCommandLog
{
    private readonly Dictionary<string, CommandEnvelope> _byMessage = new(StringComparer.Ordinal);
    private readonly Dictionary<string, CommandEnvelope> _assignmentCommands = new(StringComparer.Ordinal);

    public IReadOnlyCollection<CommandEnvelope> Commands => _byMessage.Values;

    public void Record(string assignmentId, CommandEnvelope command)
    {
        _byMessage[command.MessageId] = command;
        if (command.MessageType == MessageTypes.ExecutionAssign)
        {
            _assignmentCommands[assignmentId] = command;
        }
    }

    public bool TryGet(string assignmentId, out CommandEnvelope command) =>
        _assignmentCommands.TryGetValue(assignmentId, out command!);

    public bool TryGetMessage(string messageId, out CommandEnvelope command) =>
        _byMessage.TryGetValue(messageId, out command!);

    internal void Clear()
    {
        _byMessage.Clear();
        _assignmentCommands.Clear();
    }

    internal void Restore(IReadOnlyList<CommandEnvelope> commands)
    {
        foreach (var command in commands)
        {
            Record(command.AssignmentId, command);
        }
    }

    public IReadOnlyList<CommandEnvelope> Capture() => _byMessage.Values.ToList();
}

/// <summary>
/// A retry deferred to its backoff deadline. Retrying instantly on every
/// provider failure turns a transient fault into a hammer; doc 150 PR-012
/// requires the backoff to actually bound the attempt rate.
/// </summary>
public sealed record PendingRetry(
    string ExecutionId,
    DateTimeOffset Due,
    string WorkerId,
    string AgentId,
    IReadOnlyList<string> Capabilities,
    string PolicyRevisionId,
    TimeSpan LeaseBudget,
    string? HandoffId = null,
    string TaskContext = "{}",
    string? ProviderId = null,
    WorkspaceReference? Workspace = null,
    string? WorkItemType = null,
    int? WorkItemId = null,
    string? TaskType = null);

public sealed class PendingRetryQueue
{
    private readonly List<PendingRetry> _pending = new();
    private readonly Func<DateTimeOffset> _clock;

    public PendingRetryQueue(Func<DateTimeOffset> clock) => _clock = clock;

    public IReadOnlyList<PendingRetry> Pending => _pending;

    public void Schedule(PendingRetry retry) => _pending.Add(retry);

    /// <summary>Everything due at the current time, oldest first, without removal.</summary>
    public IReadOnlyList<PendingRetry> Due()
    {
        var now = _clock();
        return _pending.Where(r => r.Due <= now).OrderBy(r => r.Due).ToList();
    }

    /// <summary>Takes a retry out of the queue only after its dispatch succeeded.</summary>
    public void Complete(PendingRetry retry) => _pending.Remove(retry);

    internal void Clear() => _pending.Clear();

    internal void Restore(IReadOnlyList<PendingRetry> retries) => _pending.AddRange(retries);

    public IReadOnlyList<PendingRetry> Capture() => _pending.ToList();
}
