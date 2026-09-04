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
    IReadOnlyList<PendingRetry> PendingRetries);

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
        foreach (var version in state.Versions)
        {
            _versions[version.VersionId] = version;
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
    public DurableServerPlane(Func<DateTimeOffset> clock, Func<string> nextId, RetryPlanner? planner = null)
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
        Planner = planner ?? new RetryPlanner();
        Dispatcher = new CommandDispatcher(Registry, Leases, Outbox, clock, nextId, Sent);
        Results = new ServerResultProcessor(
            Registry, Leases, Inbox, Planner, DeadLetters, Dispatcher, clock, nextId, Sent, Retries);
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

    /// <summary>Dispatches every scheduled retry whose backoff has elapsed.</summary>
    public int ProcessDueRetries()
    {
        var dispatched = 0;
        foreach (var retry in Retries.TakeDue())
        {
            Dispatcher.Dispatch(
                retry.ExecutionId, retry.WorkerId, retry.AgentId,
                retry.Capabilities, retry.PolicyRevisionId, retry.LeaseBudget);
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
    public RetryPlanner Planner { get; }
    public CommandDispatcher Dispatcher { get; }
    public ServerResultProcessor Results { get; }

    public PlaneState Capture() => new(
        Registry.CaptureState(),
        Outbox.Capture(),
        Inbox.Capture(),
        Leases.Capture(),
        DeadLetters.Capture(),
        Approvals.Capture(),
        Sent.Capture(),
        Retries.Capture());

    public static DurableServerPlane Restore(Func<DateTimeOffset> clock, Func<string> nextId, PlaneState state)
    {
        var plane = new DurableServerPlane(clock, nextId);
        plane.Registry.Load(state.Registry);
        plane.Outbox.Restore(state.Outbox);
        plane.Inbox.Restore(state.Inbox);
        plane.Leases.Restore(state.Assignments);
        plane.DeadLetters.Restore(state.DeadLetters);
        plane.Approvals.Restore(state.Approvals);
        plane.Sent.Restore(state.SentCommands);
        plane.Retries.Restore(state.PendingRetries);
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
    private readonly Dictionary<string, CommandEnvelope> _byAssignment = new(StringComparer.Ordinal);

    public IReadOnlyCollection<CommandEnvelope> Commands => _byAssignment.Values;

    public void Record(string assignmentId, CommandEnvelope command) => _byAssignment[assignmentId] = command;

    public bool TryGet(string assignmentId, out CommandEnvelope command) =>
        _byAssignment.TryGetValue(assignmentId, out command!);

    internal void Clear() => _byAssignment.Clear();

    internal void Restore(IReadOnlyList<CommandEnvelope> commands)
    {
        foreach (var command in commands)
        {
            _byAssignment[command.AssignmentId] = command;
        }
    }

    public IReadOnlyList<CommandEnvelope> Capture() => _byAssignment.Values.ToList();
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
    TimeSpan LeaseBudget);

public sealed class PendingRetryQueue
{
    private readonly List<PendingRetry> _pending = new();
    private readonly Func<DateTimeOffset> _clock;

    public PendingRetryQueue(Func<DateTimeOffset> clock) => _clock = clock;

    public IReadOnlyList<PendingRetry> Pending => _pending;

    public void Schedule(PendingRetry retry) => _pending.Add(retry);

    /// <summary>Removes and returns everything due at the current time, oldest first.</summary>
    public IReadOnlyList<PendingRetry> TakeDue()
    {
        var now = _clock();
        var due = _pending.Where(r => r.Due <= now).OrderBy(r => r.Due).ToList();
        foreach (var retry in due)
        {
            _pending.Remove(retry);
        }

        return due;
    }

    internal void Clear() => _pending.Clear();

    internal void Restore(IReadOnlyList<PendingRetry> retries) => _pending.AddRange(retries);

    public IReadOnlyList<PendingRetry> Capture() => _pending.ToList();
}
