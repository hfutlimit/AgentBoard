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
    IReadOnlyList<ApprovalRequest> Approvals);

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
        Planner = planner ?? new RetryPlanner();
        Dispatcher = new CommandDispatcher(Registry, Leases, Outbox, clock, nextId);
        Results = new ServerResultProcessor(Registry, Leases, Inbox, Planner, DeadLetters, Dispatcher, clock, nextId);
    }

    internal Func<DateTimeOffset> Clock { get; }
    internal Func<string> NextId { get; }

    public WorkflowRegistry Registry { get; }
    public LeaseRegistry Leases { get; }
    public Inbox Inbox { get; }
    public ServerOutbox Outbox { get; }
    public DeadLetterQueue DeadLetters { get; }
    public ApprovalInbox Approvals { get; }
    public RetryPlanner Planner { get; }
    public CommandDispatcher Dispatcher { get; }
    public ServerResultProcessor Results { get; }

    public PlaneState Capture() => new(
        Registry.CaptureState(),
        Outbox.Capture(),
        Inbox.Capture(),
        Leases.Capture(),
        DeadLetters.Capture(),
        Approvals.Capture());

    public static DurableServerPlane Restore(Func<DateTimeOffset> clock, Func<string> nextId, PlaneState state)
    {
        var plane = new DurableServerPlane(clock, nextId);
        plane.Registry.Load(state.Registry);
        plane.Outbox.Restore(state.Outbox);
        plane.Inbox.Restore(state.Inbox);
        plane.Leases.Restore(state.Assignments);
        plane.DeadLetters.Restore(state.DeadLetters);
        plane.Approvals.Restore(state.Approvals);
        return plane;
    }
}
