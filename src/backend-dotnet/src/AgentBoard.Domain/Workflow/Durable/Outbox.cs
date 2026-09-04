// SPDX-License-Identifier: MIT
using System.Text.Json;
using AgentBoard.Contracts;
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Workflow.Durable;

/// <summary>Lifecycle of one row in the Server's transactional outbox.</summary>
/// <remarks>
/// doc 151 §6.1 puts the outbox row inside the same database transaction as
/// the authoritative state update, and only the broker confirm promotes a
/// message past <see cref="Published"/>. "Written to an in-memory queue" is
/// never one of these states — that is precisely the confusion doc 151 §5.5
/// forbids ("不能以写入内存队列作为 durable accept").
/// </remarks>
public enum OutboxState
{
    /// <summary>Committed with the state change, not yet handed to the broker.</summary>
    Pending,

    /// <summary>Handed to the broker; confirm has not come back yet.</summary>
    Published,

    /// <summary>Broker confirmed. The message may be pruned after the retention window.</summary>
    Confirmed,

    /// <summary>Exhausted its retry budget and awaits operator action.</summary>
    DeadLettered,
}

/// <summary>
/// One durable command awaiting (or having completed) broker confirmation
/// (doc 150 PR-006, doc 151 §6.1).
/// </summary>
public sealed record OutboxMessage(
    string MessageId,
    string IdempotencyKey,
    string MessageType,
    string CorrelationId,
    string Payload,
    OutboxState State,
    int AttemptCount,
    DateTimeOffset CreatedAt,
    DateTimeOffset? NextAttemptAt,
    DateTimeOffset? ConfirmedAt,
    string? LastError)
{
    public static OutboxMessage NewCommand(CommandEnvelope command, DateTimeOffset now)
    {
        var errors = EnvelopeValidator.Validate(command);
        if (errors.Count > 0)
        {
            throw new InvalidValueException(
                $"refusing to outbox an invalid command: {string.Join("; ", errors.Select(e => $"{e.Field} {e.Reason}"))}");
        }

        return new OutboxMessage(
            command.MessageId,
            command.IdempotencyKey,
            command.MessageType,
            command.CorrelationId,
            JsonSerializer.Serialize(command),
            OutboxState.Pending,
            AttemptCount: 0,
            CreatedAt: now,
            NextAttemptAt: now,
            ConfirmedAt: null,
            LastError: null);
    }

    /// <summary>True when the dispatcher may hand this message to the broker now.</summary>
    public bool IsDue(DateTimeOffset now) =>
        (State == OutboxState.Pending || State == OutboxState.Published)
        && NextAttemptAt.HasValue
        && NextAttemptAt.Value <= now;
}

/// <summary>
/// The Server-side durable outbox. Adds happen inside the same unit of work as
/// the registry mutation they accompany; the dispatcher never invents messages.
/// </summary>
public sealed partial class ServerOutbox
{
    private readonly Dictionary<string, OutboxMessage> _messages = new(StringComparer.Ordinal);
    private readonly Func<DateTimeOffset> _clock;

    public ServerOutbox(Func<DateTimeOffset> clock)
    {
        _clock = clock ?? throw new ArgumentNullException(nameof(clock));
    }

    public IReadOnlyCollection<OutboxMessage> Messages => _messages.Values;

    public OutboxMessage Add(OutboxMessage message)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(message.MessageId);

        if (!_messages.TryAdd(message.MessageId, message))
        {
            throw new DuplicateException($"outbox message '{message.MessageId}' already exists");
        }

        return message;
    }

    public OutboxMessage AddCommand(CommandEnvelope command) => Add(OutboxMessage.NewCommand(command, _clock()));

    public OutboxMessage Require(string messageId) =>
        _messages.TryGetValue(messageId, out var message)
            ? message
            : throw new NotFoundException($"outbox message '{messageId}' not found");

    /// <summary>
    /// Replaces a message; used by the dispatcher after each publish attempt.
    /// </summary>
    public void Replace(OutboxMessage message) => _messages[message.MessageId] = message;

    public IReadOnlyList<OutboxMessage> DueMessages(DateTimeOffset now) =>
        _messages.Values.Where(m => m.IsDue(now)).OrderBy(m => m.CreatedAt).ToList();

    /// <summary>
    /// Durably claims a due row before network I/O. The recovery deadline makes
    /// a Server crash after the claim safe: the Published row becomes due again
    /// and at-least-once delivery plus Node inbox dedup closes the ambiguity.
    /// </summary>
    public OutboxMessage BeginDispatch(
        string messageId,
        DateTimeOffset now,
        TimeSpan? recoveryWindow = null)
    {
        var message = Require(messageId);
        if (!message.IsDue(now))
        {
            throw new InvalidValueException($"outbox message '{messageId}' is not due");
        }

        var attempted = message with
        {
            State = OutboxState.Published,
            AttemptCount = message.AttemptCount + 1,
            NextAttemptAt = now + (recoveryWindow ?? TimeSpan.FromMinutes(1)),
        };
        Replace(attempted);
        return attempted;
    }

    public IReadOnlyList<OutboxMessage> BeginDueDispatches(
        DateTimeOffset now,
        int maximum = int.MaxValue,
        TimeSpan? recoveryWindow = null) =>
        DueMessages(now)
            .Take(Math.Max(0, maximum))
            .Select(message => BeginDispatch(message.MessageId, now, recoveryWindow))
            .ToList();

    /// <summary>Applies the broker result only to the exact claimed attempt.</summary>
    public OutboxState CompleteDispatch(
        OutboxMessage attempted,
        PublishResult outcome,
        DateTimeOffset now,
        RetryPlanner planner,
        DeadLetterQueue deadLetters,
        string? publishError = null)
    {
        var current = Require(attempted.MessageId);
        if (current.State != OutboxState.Published || current.AttemptCount != attempted.AttemptCount)
        {
            return current.State;
        }

        if (outcome == PublishResult.Confirmed)
        {
            var done = current with
            {
                State = OutboxState.Confirmed,
                NextAttemptAt = null,
                ConfirmedAt = now,
                LastError = null,
            };
            Replace(done);
            return done.State;
        }

        var decision = planner.Decide(FailureCategory.TransportFailure, current.AttemptCount);
        if (decision.IsRetry)
        {
            var retry = current with
            {
                State = OutboxState.Pending,
                NextAttemptAt = now + decision.Delay!.Value,
                LastError = publishError ?? "publish not confirmed",
            };
            Replace(retry);
            return retry.State;
        }

        var dead = current with
        {
            State = OutboxState.DeadLettered,
            NextAttemptAt = null,
            LastError = $"{publishError ?? "publish not confirmed"}; retry budget exhausted",
        };
        Replace(dead);
        deadLetters.Enqueue(new DeadLetterEntry(
            Id: $"dlq-{dead.MessageId}",
            MessageId: dead.MessageId,
            ExecutionId: null,
            Category: FailureCategory.TransportFailure,
            Reason: dead.LastError,
            EnqueuedAt: now));
        return dead.State;
    }
}

/// <summary>Broker seam for the outbox dispatcher.</summary>
/// <remarks>
/// A single method on purpose: the Node's result outbox has the identical
/// confirm-then-progress shape, so both sides share the semantics without
/// either depending on RabbitMQ types at the domain layer.
/// </remarks>
public interface ICommandTransport
{
    /// <summary>
    /// Publishes and waits for the broker confirm. Returning
    /// <see cref="PublishResult.Failed"/> covers both a nack and a confirm
    /// timeout (doc 151 §6.3).
    /// </summary>
    PublishResult Publish(OutboxMessage message);
}

public enum PublishResult { Confirmed, Failed }

/// <summary>
/// Drives outbox messages to their terminal state using the retry planner.
/// </summary>
/// <remarks>
/// doc 151 §5.5: a broker confirm, not an enqueue, is the durable acceptance.
/// Every failed publish is classified as a <see cref="FailureCategory.TransportFailure"/>
/// so it follows the shared retry/DLQ rules rather than getting bespoke
/// handling (doc 150 PR-012).
/// </remarks>
public sealed class OutboxDispatcher
{
    private readonly ServerOutbox _outbox;
    private readonly ICommandTransport _transport;
    private readonly RetryPlanner _planner;
    private readonly DeadLetterQueue _deadLetters;
    private readonly Func<DateTimeOffset> _clock;

    public OutboxDispatcher(
        ServerOutbox outbox,
        ICommandTransport transport,
        RetryPlanner planner,
        DeadLetterQueue deadLetters,
        Func<DateTimeOffset> clock)
    {
        _outbox = outbox;
        _transport = transport;
        _planner = planner;
        _deadLetters = deadLetters;
        _clock = clock;
    }

    /// <summary>
    /// Attempts every due message once. Returns how many reached
    /// <see cref="OutboxState.Confirmed"/> during this pass.
    /// </summary>
    public int DispatchDue()
    {
        var now = _clock();
        var confirmed = 0;

        foreach (var message in _outbox.DueMessages(now))
        {
            if (TryDispatchOne(message, now) == OutboxState.Confirmed)
            {
                confirmed++;
            }
        }

        return confirmed;
    }

    public OutboxState TryDispatchOne(OutboxMessage message, DateTimeOffset now)
    {
        var attempted = _outbox.BeginDispatch(message.MessageId, now);

        PublishResult outcome;
        string? publishError = null;
        try
        {
            outcome = _transport.Publish(attempted);
        }
        catch (Exception error)
        {
            outcome = PublishResult.Failed;
            publishError = $"publish threw {error.GetType().Name}";
        }

        return _outbox.CompleteDispatch(
            attempted, outcome, now, _planner, _deadLetters, publishError);
    }
}
