// SPDX-License-Identifier: MIT
using System.Text.Json;
using AgentBoard.Contracts;

namespace AgentBoard.Node.Durable;

/// <summary>
/// Local durable home for received commands. The receiver ACKs the broker
/// message only after this journal has the command — an append that throws
/// means "not accepted", forcing redelivery (doc 151 §6.1: inbox/dedup
/// durable accept precedes broker ACK).
/// </summary>
/// <summary>What an atomic journal attempt ended as.</summary>
public enum JournalAttempt { Accepted, Duplicate }

/// <summary>
/// Local durable home for received commands. The receiver ACKs the broker
/// message only after this journal holds the command — an append that throws
/// means "not accepted", forcing redelivery (doc 151 §6.1: inbox/dedup durable
/// accept precedes broker ACK).
/// </summary>
/// <remarks>
/// <see cref="TryAccept"/> takes both dedup keys in ONE call on purpose: the
/// message-key and business-key rows must appear together or not at all. A
/// two-step append could die between the halves, and redelivery would then be
/// answered "duplicate" (and ACKed) while the assignment side of the accept
/// never persisted — the command would vanish durably. Implementations that
/// span a store must wrap the pair in one transaction.
/// </remarks>
public interface INodeCommandJournal
{
    /// <summary>
    /// Durably records the command under both keys, atomically. Throws on
    /// storage failure — throwing is how "not accepted" reaches the consumer.
    /// </summary>
    JournalAttempt TryAccept(CommandEnvelope command, string messageKey, string businessKey);

    IReadOnlyList<CommandEnvelope> All();
}

public sealed class InMemoryNodeCommandJournal : INodeCommandJournal
{
    private readonly Dictionary<string, CommandEnvelope> _byKey = new(StringComparer.Ordinal);
    private readonly object _gate = new();

    public JournalAttempt TryAccept(CommandEnvelope command, string messageKey, string businessKey)
    {
        lock (_gate)
        {
            if (_byKey.ContainsKey(messageKey) || _byKey.ContainsKey(businessKey))
            {
                return JournalAttempt.Duplicate;
            }

            // Both keys written before the lock releases: one visible unit.
            _byKey[messageKey] = command;
            _byKey[businessKey] = command;
            return JournalAttempt.Accepted;
        }
    }

    public IReadOnlyList<CommandEnvelope> All()
    {
        lock (_gate)
        {
            return _byKey.Values.ToList();
        }
    }
}

public enum AcceptanceKind
{
    Accepted,
    Duplicate,
    RejectedSchema,
    RejectedNotForThisWorker,
    RejectedLeaseMismatch,
}

/// <summary>
/// The answer to a broker delivery. <see cref="ShouldAckBroker"/> is false for
/// every rejection and acceptance failure — only duplicates and accepted
/// commands are ACKed, because those are states the Node can durably attest.
/// </summary>
public sealed record CommandAcceptance(
    AcceptanceKind Kind,
    string Reason,
    bool ShouldAckBroker,
    CommandEnvelope? Command = null);

/// <summary>
/// The Node's local record of the assignment it is working under. doc 151
/// §5.4: the Node must record assignment and epoch locally and stop
/// submitting business results once its lease is superseded or expired.
/// </summary>
public sealed class AssignmentTracker
{
    private readonly Dictionary<string, Assignment> _byExecution = new(StringComparer.Ordinal);
    private readonly Dictionary<string, Assignment> _byId = new(StringComparer.Ordinal);

    public IReadOnlyCollection<Assignment> Current => _byExecution.Values;

    public Assignment? CurrentFor(string executionId) =>
        _byExecution.TryGetValue(executionId, out var assignment) ? assignment : null;

    /// <summary>
    /// Learns the assignment carried by an assign command (its payload is the
    /// assignment JSON the Server dispatched).
    /// </summary>
    public void Apply(CommandEnvelope command)
    {
        if (command.MessageType != MessageTypes.ExecutionAssign)
        {
            return;
        }

        var assignment = ParseAssignment(command);

        // Higher or equal epoch replaces; the Node never keeps two live
        // assignments for one execution.
        _byExecution[assignment.ExecutionId] = assignment;
        _byId[assignment.AssignmentId] = assignment;
    }

    public static Assignment ParseAssignment(CommandEnvelope command) =>
        JsonSerializer.Deserialize<Assignment>(command.Payload)
        ?? throw new InvalidOperationException("assign command payload did not carry an assignment");

    /// <summary>
    /// True when the Node still holds the right to submit results for this
    /// assignment: it must be the recorded one and inside its lease window.
    /// </summary>
    public bool MaySubmitResult(string assignmentId, DateTimeOffset now)
    {
        if (!_byId.TryGetValue(assignmentId, out var assignment))
        {
            return false;
        }

        var stillCurrent = _byExecution.TryGetValue(assignment.ExecutionId, out var live)
            && string.Equals(live.AssignmentId, assignmentId, StringComparison.Ordinal);

        return stillCurrent && !assignment.IsExpired(now);
    }

    /// <summary>Assignments whose lease elapsed; the runner must stop and release them.</summary>
    public IReadOnlyList<Assignment> ExpiredAssignments(DateTimeOffset now) =>
        _byExecution.Values.Where(a => a.IsExpired(now)).ToList();

    /// <summary>Drops an expired assignment so its (late) result is never committed.</summary>
    public void Release(Assignment assignment)
    {
        if (_byExecution.TryGetValue(assignment.ExecutionId, out var live) && live.AssignmentId == assignment.AssignmentId)
        {
            _byExecution.Remove(assignment.ExecutionId);
        }
    }
}

/// <summary>
/// The Node's command inbox: schema/version/worker/lease/policy validation,
/// then durable accept, then (and only then) an ACK answer
/// (doc 151 §5.5 command rules).
/// </summary>
public sealed class NodeCommandReceiver
{
    private readonly string _workerId;
    private readonly INodeCommandJournal _journal;
    private readonly AssignmentTracker _tracker;
    private readonly Func<DateTimeOffset> _clock;

    public NodeCommandReceiver(
        string workerId,
        INodeCommandJournal journal,
        AssignmentTracker tracker,
        Func<DateTimeOffset> clock)
    {
        _workerId = workerId;
        _journal = journal;
        _tracker = tracker;
        _clock = clock;
    }

    public static string MessageKey(string messageId) => $"msg:{messageId}";
    public static string BusinessKey(string idempotencyKey) => $"idem:{idempotencyKey}";

    public CommandAcceptance TryAccept(CommandEnvelope command)
    {
        var errors = EnvelopeValidator.Validate(command);
        if (errors.Count > 0)
        {
            return new CommandAcceptance(AcceptanceKind.RejectedSchema,
                string.Join("; ", errors.Select(e => $"{e.Field} {e.Reason}")),
                ShouldAckBroker: false);
        }

        if (!string.Equals(command.WorkerId, _workerId, StringComparison.Ordinal))
        {
            // Misaddressed work is refused, not executed, and not ACKed — the
            // broker must hand it to whoever owns it (fail closed).
            return new CommandAcceptance(AcceptanceKind.RejectedNotForThisWorker,
                $"command targets worker '{command.WorkerId}', not '{_workerId}'",
                ShouldAckBroker: false);
        }

        // A cancel command must name the assignment currently held for its
        // execution; an assign against a known-but-superseded lease is
        // refused. Brand-new assignments (empty local record) pass through.
        var known = _tracker.CurrentFor(command.ExecutionId);
        if (known is not null && command.MessageType == MessageTypes.ExecutionAssign)
        {
            var stale = AssignmentValidator.ValidateCommandAgainstAssignment(command, known);
            if (stale.Count > 0 && command.LeaseEpoch <= known.LeaseEpoch)
            {
                return new CommandAcceptance(AcceptanceKind.RejectedLeaseMismatch,
                    string.Join("; ", stale.Select(e => $"{e.Field} {e.Reason}")),
                    ShouldAckBroker: false);
            }
        }

        // One atomic call for both dedup keys; a storage exception escapes and
        // the broker redelivers, so there is no window where the message looks
        // accepted locally but is not.
        var outcome = _journal.TryAccept(command, MessageKey(command.MessageId), BusinessKey(command.IdempotencyKey));

        if (outcome == JournalAttempt.Duplicate)
        {
            // Duplicate: the durable record already exists, so the broker may
            // discard its copy — but nothing re-runs (doc 150 PR-007).
            return new CommandAcceptance(AcceptanceKind.Duplicate,
                "command already durably accepted", ShouldAckBroker: true, command);
        }

        _tracker.Apply(command);
        return new CommandAcceptance(AcceptanceKind.Accepted,
            $"accepted under lease epoch {command.LeaseEpoch} at {_clock():O}",
            ShouldAckBroker: true, command);
    }

    /// <summary>
    /// Rebuilds local assignment state after a restart from the durable
    /// journal, releasing leases that expired while the Node was offline so a
    /// stale local process can never submit a result (A2 exit criterion:
    /// restart either continues or explicitly releases the assignment).
    /// </summary>
    public IReadOnlyList<Assignment> RebuildAfterRestart()
    {
        foreach (var command in _journal.All())
        {
            _tracker.Apply(command);
        }

        var now = _clock();
        var expired = _tracker.ExpiredAssignments(now);
        foreach (var assignment in expired)
        {
            _tracker.Release(assignment);
        }

        return expired;
    }
}
