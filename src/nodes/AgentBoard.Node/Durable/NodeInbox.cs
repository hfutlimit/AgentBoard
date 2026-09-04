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

public enum JournalCommandState { Pending, Completed }

public sealed record JournaledCommand(CommandEnvelope Command, JournalCommandState State);

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

    IReadOnlyList<CommandEnvelope> Pending();

    void MarkCompleted(string messageId);
}

public sealed class InMemoryNodeCommandJournal : INodeCommandJournal
{
    private readonly Dictionary<string, CommandEnvelope> _byKey = new(StringComparer.Ordinal);
    private readonly Dictionary<string, JournalCommandState> _stateByMessage = new(StringComparer.Ordinal);
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
            _stateByMessage[command.MessageId] = JournalCommandState.Pending;
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

    public IReadOnlyList<CommandEnvelope> Pending()
    {
        lock (_gate)
        {
            return _byKey.Values
                .DistinctBy(command => command.MessageId)
                .Where(command => _stateByMessage.GetValueOrDefault(command.MessageId) == JournalCommandState.Pending)
                .ToList();
        }
    }

    public void MarkCompleted(string messageId)
    {
        lock (_gate)
        {
            if (!_stateByMessage.ContainsKey(messageId))
            {
                throw new KeyNotFoundException($"journal command '{messageId}' was not accepted");
            }

            _stateByMessage[messageId] = JournalCommandState.Completed;
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
    RejectedExpired,
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
    private readonly Dictionary<string, string> _handoffByAssignment = new(StringComparer.Ordinal);
    private readonly object _gate = new();

    public IReadOnlyCollection<Assignment> Current
    {
        get
        {
            lock (_gate) { return _byExecution.Values.ToArray(); }
        }
    }

    public Assignment? CurrentFor(string executionId)
    {
        lock (_gate)
        {
            return _byExecution.TryGetValue(executionId, out var assignment) ? assignment : null;
        }
    }

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

        var payload = ParseAssignPayload(command);
        Apply(payload.Assignment, payload.HandoffId);
    }

    /// <summary>Recording an already-parsed assignment cannot fail halfway.</summary>
    public void Apply(Assignment assignment, string? handoffId = null)
    {
        lock (_gate)
        {
            // Higher or equal epoch replaces; the Node never keeps two live
            // assignments for one execution.
            if (_byExecution.TryGetValue(assignment.ExecutionId, out var current)
                && assignment.LeaseEpoch < current.LeaseEpoch)
            {
                return;
            }

            _byExecution[assignment.ExecutionId] = assignment;
            _byId[assignment.AssignmentId] = assignment;
            if (!string.IsNullOrWhiteSpace(handoffId))
            {
                _handoffByAssignment[assignment.AssignmentId] = handoffId;
            }
        }
    }

    /// <summary>The handoff is bound to its assignment, never to global arrival order.</summary>
    public string? HandoffFor(string assignmentId)
    {
        lock (_gate)
        {
            return _handoffByAssignment.TryGetValue(assignmentId, out var handoffId) ? handoffId : null;
        }
    }

    /// <summary>
    /// Reads the assignment from an assign command. Server dispatches wrap it
    /// in <see cref="AssignCommandPayload"/> (which may carry a handoff id);
    /// an older bare-assignment payload still parses, so a mixed-version
    /// broker conversation degrades by parse order, not by guessing
    /// (doc 151 §11 minor-compatibility rule).
    /// </summary>
    public static AssignCommandPayload ParseAssignPayload(CommandEnvelope command)
    {
        var wrapped = JsonSerializer.Deserialize<AssignCommandPayload>(
            command.Payload, new JsonSerializerOptions(JsonSerializerDefaults.Web));

        if (wrapped?.Assignment is { } assignment && AssignmentValidator.IsValid(assignment))
        {
            return wrapped;
        }

        var legacy = JsonSerializer.Deserialize<Assignment>(command.Payload)
            ?? throw new InvalidOperationException("assign command payload did not carry an assignment");
        return new AssignCommandPayload(legacy);
    }

    public static Assignment ParseAssignment(CommandEnvelope command) => ParseAssignPayload(command).Assignment;

    /// <summary>
    /// True when the Node still holds the right to submit results for this
    /// assignment: it must be the recorded one and inside its lease window.
    /// </summary>
    public bool MaySubmitResult(string assignmentId, DateTimeOffset now)
    {
        lock (_gate)
        {
            if (!_byId.TryGetValue(assignmentId, out var assignment))
            {
                return false;
            }

            var stillCurrent = _byExecution.TryGetValue(assignment.ExecutionId, out var live)
                && string.Equals(live.AssignmentId, assignmentId, StringComparison.Ordinal);

            return stillCurrent && !assignment.IsExpired(now);
        }
    }

    /// <summary>Assignments whose lease elapsed; the runner must stop and release them.</summary>
    public IReadOnlyList<Assignment> ExpiredAssignments(DateTimeOffset now)
    {
        lock (_gate)
        {
            return _byExecution.Values.Where(a => a.IsExpired(now)).ToList();
        }
    }

    /// <summary>Drops an expired assignment so its (late) result is never committed.</summary>
    public void Release(Assignment assignment)
    {
        lock (_gate)
        {
            if (_byExecution.TryGetValue(assignment.ExecutionId, out var live)
                && live.AssignmentId == assignment.AssignmentId)
            {
                _byExecution.Remove(assignment.ExecutionId);
            }
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

    /// <summary>Handoff reference carried by the last accepted assign command.</summary>
    public string? LastHandoffId { get; private set; }

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


        if (command.ExpiresAt <= _clock())
        {
            // An expired command can never become executable. ACK it as a
            // terminal stale delivery; requeueing cannot make its lease valid
            // again and would create an infinite broker loop.
            return new CommandAcceptance(AcceptanceKind.RejectedExpired,
                $"command lease expired at {command.ExpiresAt:O}",
                ShouldAckBroker: true);
        }

        // The assignment a command carries is parsed and cross-checked BEFORE
        // the journal write. If the payload were only interpreted after the
        // durable accept, a malformed assignment would burn the dedup keys:
        // redelivery would answer "duplicate" (and ACK) while the tracker
        // never learned the lease, swallowing the command (doc 151 §5.5: the
        // Node validates before it accepts).
        AssignCommandPayload? parsedPayload = null;
        if (command.MessageType == MessageTypes.ExecutionAssign)
        {
            try
            {
                parsedPayload = AssignmentTracker.ParseAssignPayload(command);
            }
            catch (Exception e)
            {
                return new CommandAcceptance(AcceptanceKind.RejectedSchema,
                    $"assign payload is not a readable assignment: {e.Message}", ShouldAckBroker: false);
            }

            var shapeErrors = AssignmentValidator.Validate(parsedPayload.Assignment)
                .Concat(AssignmentValidator.ValidateCommandAgainstAssignment(command, parsedPayload.Assignment))
                .ToList();

            if (parsedPayload.Handoff is not null)
            {
                shapeErrors.AddRange(EnvelopeValidator.Validate(parsedPayload.Handoff));
                if (!string.Equals(parsedPayload.HandoffId, parsedPayload.Handoff.HandoffId, StringComparison.Ordinal))
                {
                    shapeErrors.Add(new EnvelopeError(nameof(parsedPayload.HandoffId),
                        "must equal the embedded handoff context id"));
                }

                var offered = new HashSet<string>(parsedPayload.Assignment.RequiredCapabilities, StringComparer.Ordinal);
                if (parsedPayload.Handoff.RequiredCapabilities.Any(capability => !offered.Contains(capability)))
                {
                    shapeErrors.Add(new EnvelopeError(nameof(parsedPayload.Assignment.RequiredCapabilities),
                        "does not satisfy the embedded handoff"));
                }
            }
            if (shapeErrors.Count > 0)
            {
                return new CommandAcceptance(AcceptanceKind.RejectedSchema,
                    "assignment does not match its command: " +
                    string.Join("; ", shapeErrors.Select(e => $"{e.Field} {e.Reason}")),
                    ShouldAckBroker: false);
            }
        }

        // A cancel command must name the assignment currently held for its
        // execution; an assign against a known-but-superseded lease is
        // refused. Brand-new assignments (empty local record) pass through.
        var known = _tracker.CurrentFor(command.ExecutionId);
        if (command.MessageType == MessageTypes.ExecutionCancel)
        {
            if (known is null)
            {
                return new CommandAcceptance(AcceptanceKind.RejectedLeaseMismatch,
                    $"cancel names unknown execution '{command.ExecutionId}'",
                    ShouldAckBroker: true);
            }

            var mismatch = AssignmentValidator.ValidateCommandAgainstAssignment(command, known);
            if (mismatch.Count > 0)
            {
                return new CommandAcceptance(AcceptanceKind.RejectedLeaseMismatch,
                    string.Join("; ", mismatch.Select(e => $"{e.Field} {e.Reason}")),
                    ShouldAckBroker: true);
            }
        }
        else if (known is not null && command.MessageType == MessageTypes.ExecutionAssign)
        {
            var stale = AssignmentValidator.ValidateCommandAgainstAssignment(command, known);
            if (stale.Count > 0 && command.LeaseEpoch <= known.LeaseEpoch)
            {
                return new CommandAcceptance(AcceptanceKind.RejectedLeaseMismatch,
                    string.Join("; ", stale.Select(e => $"{e.Field} {e.Reason}")),
                    ShouldAckBroker: true);
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

        // The journal row and the parsed assignment now land together;
        // Apply(Assignment) cannot throw because parsing already succeeded.
        if (parsedPayload is not null)
        {
            _tracker.Apply(parsedPayload.Assignment, parsedPayload.HandoffId);
            LastHandoffId = parsedPayload.HandoffId;
        }
        else
        {
            LastHandoffId = null;
        }

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
