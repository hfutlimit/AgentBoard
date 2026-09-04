// SPDX-License-Identifier: MIT
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using AgentBoard.Contracts;

namespace AgentBoard.Node.Durable;

/// <summary>
/// Redaction seam for anything stored as a local detail event
/// (doc 150 PR-009, PR-015; ADR-0002 named the secret classes to strip).
/// </summary>
public interface IRedactionPolicy
{
    string Redact(string text);

    /// <summary>Classifies what was redacted, for audit; empty when nothing hit.</summary>
    IReadOnlyList<string> Findings { get; }
}

public sealed class NoOpRedaction : IRedactionPolicy
{
    public string Redact(string text) => text;

    public IReadOnlyList<string> Findings => Array.Empty<string>();
}

/// <summary>
/// Pattern-based redaction for the common secret shapes: bearer tokens,
/// provider API keys, PEM blocks, and key=value credential assignments.
/// It is a floor, not a ceiling — adapters may redact further (doc 150
/// PR-015 requires classification before storage, not merely this list).
/// </summary>
public sealed class SecretRedaction : IRedactionPolicy
{
    private static readonly (string Name, Regex Pattern)[] Rules =
    {
        ("bearer", new Regex("(?i)bearer\\s+[a-z0-9._~+/-]{12,}", RegexOptions.Compiled)),
        ("api-key", new Regex("(?i)\\b(sk|api|key|token|secret|password)[a-z0-9_-]{8,}\\b", RegexOptions.Compiled)),
        ("credential-assignment", new Regex(
            "(?i)\\b(api[_-]?key|auth[_-]?token|client[_-]?secret|password|passwd)\\b\\s*[=:]\\s*\\S+",
            RegexOptions.Compiled)),
        ("pem", new Regex("-----BEGIN [A-Z ]*PRIVATE KEY-----[\\s\\S]*?-----END [A-Z ]*PRIVATE KEY-----",
            RegexOptions.Compiled)),
    };

    private readonly List<string> _findings = new();

    public IReadOnlyList<string> Findings => _findings;

    public string Redact(string text)
    {
        var result = text;

        foreach (var (name, pattern) in Rules)
        {
            result = pattern.Replace(result, m =>
            {
                _findings.Add(name);
                return $"[REDACTED:{name}]";
            });
        }

        return result;
    }
}

public enum EventAppendKind { Stored, Duplicate, RejectedSchema }

/// <summary>
/// The Node's local detail event store (doc 151 §5.7, §8.2). Detailed events
/// stay here; only permitted summaries or references cross to the Server.
/// Dedup is on source + event_id, as the frozen contract requires.
/// </summary>
/// <summary>
/// Storage seam for detail events. The A2 exit criteria say local detail
/// must SURVIVE a Node restart; keeping the dictionary as the only home made
/// the store an in-memory demo. The SQLite implementation in
/// <see cref="SqliteEventSink"/> is the production shape; in-memory stays for
/// tests and ephemeral development runs.
/// </summary>
public interface IEventSink
{
    bool TryInsert(EventEnvelope envelope);

    EventEnvelope? Find(string dedupKey);

    IReadOnlyList<EventEnvelope> All();

    int Count();
}

public sealed class InMemoryEventSink : IEventSink
{
    private readonly Dictionary<string, EventEnvelope> _byDedupKey = new(StringComparer.Ordinal);
    private readonly object _gate = new();

    public bool TryInsert(EventEnvelope envelope)
    {
        lock (_gate)
        {
            return _byDedupKey.TryAdd(envelope.DedupKey, envelope);
        }
    }

    public EventEnvelope? Find(string dedupKey)
    {
        lock (_gate)
        {
            return _byDedupKey.TryGetValue(dedupKey, out var e) ? e : null;
        }
    }

    public IReadOnlyList<EventEnvelope> All()
    {
        lock (_gate)
        {
            return _byDedupKey.Values.ToList();
        }
    }

    public int Count()
    {
        lock (_gate)
        {
            return _byDedupKey.Count;
        }
    }
}

public sealed class LocalEventStore
{
    private readonly IEventSink _sink;
    private readonly IRedactionPolicy _redaction;

    public LocalEventStore(IRedactionPolicy? redaction = null, IEventSink? sink = null)
    {
        _redaction = redaction ?? new SecretRedaction();
        _sink = sink ?? new InMemoryEventSink();
    }

    public int Count => _sink.Count();

    public EventAppendKind TryAppend(EventEnvelope envelope, out EventEnvelope stored, out string reason)
    {
        var errors = EnvelopeValidator.Validate(envelope);
        if (errors.Count > 0)
        {
            stored = envelope;
            reason = string.Join("; ", errors.Select(e => $"{e.Field} {e.Reason}"));
            return EventAppendKind.RejectedSchema;
        }

        // Redaction happens BEFORE any write: the persisted form must never
        // contain the secret even briefly (doc 150 PR-015).
        stored = envelope with { Data = _redaction.Redact(envelope.Data) };

        if (!_sink.TryInsert(stored))
        {
            stored = _sink.Find(stored.DedupKey) ?? stored;
            reason = "duplicate event id for this source";
            return EventAppendKind.Duplicate;
        }

        reason = "stored";
        return EventAppendKind.Stored;
    }

    public IReadOnlyList<EventEnvelope> ForAttempt(string attemptId) =>
        _sink.All()
            .Where(e => string.Equals(e.Subject, attemptId, StringComparison.Ordinal))
            .OrderBy(e => e.Time)
            .ToList();
}

public enum LocalOutboxState { Pending, Published, Confirmed, DeadLettered }

/// <summary>One result awaiting broker confirmation from the Node side.</summary>
public sealed record LocalOutboxRecord(
    string MessageId,
    string IdempotencyKey,
    ResultEnvelope Result,
    LocalOutboxState State,
    int AttemptCount,
    DateTimeOffset CreatedAt,
    DateTimeOffset? NextAttemptAt,
    DateTimeOffset? ConfirmedAt,
    string? LastError);

/// <summary>Broker confirm outcome, mirrored locally so the Node does not
/// depend on the Server's domain assembly (the two ends share Contracts only).
/// </summary>
public enum BrokerConfirm { Confirmed, Failed }

public interface IResultTransport
{
    /// <summary>Publish and await broker confirm, same contract as the Server side.</summary>
    BrokerConfirm Publish(LocalOutboxRecord record);
}

/// <summary>
/// The Node's durable result outbox (doc 151 §6.2): the attempt's outcome is
/// written here before any broker publish, so a Node crash after completing
/// work never loses the result, and a redelivery is deduped at the Server.
/// </summary>
/// <summary>Durable home for the result outbox; the SQLite implementation
/// is what makes "the Node completed work but crashed" recoverable
/// (doc 151 §6.2: attempt results land locally BEFORE any publish).</summary>
public interface IResultOutboxLog
{
    void Save(LocalOutboxRecord record);

    IReadOnlyList<LocalOutboxRecord> LoadAll();
}

public sealed class LocalResultOutbox
{
    private readonly Dictionary<string, LocalOutboxRecord> _records = new(StringComparer.Ordinal);
    private readonly IResultTransport _transport;
    private readonly Func<DateTimeOffset> _clock;
    private readonly int _maxAttempts;
    private readonly TimeSpan _baseDelay;
    private readonly TimeSpan _maxDelay;
    private readonly IResultOutboxLog? _log;

    public LocalResultOutbox(
        IResultTransport transport,
        Func<DateTimeOffset> clock,
        int maxAttempts = 8,
        TimeSpan? baseDelay = null,
        TimeSpan? maxDelay = null,
        IResultOutboxLog? log = null)
    {
        _transport = transport;
        _clock = clock;
        _maxAttempts = maxAttempts;
        _baseDelay = baseDelay ?? TimeSpan.FromSeconds(2);
        _maxDelay = maxDelay ?? TimeSpan.FromMinutes(5);
        _log = log;

        // A restart re-adopts everything the log still owes: confirmed rows
        // stay terminal, pending/published rows go back through the transport
        // under their ORIGINAL message and idempotency keys (doc 150 PR-007).
        if (_log is not null)
        {
            foreach (var record in _log.LoadAll())
            {
                _records[record.MessageId] = record;
            }
        }
    }

    public IReadOnlyCollection<LocalOutboxRecord> Records => _records.Values;

    /// <summary>Every state change is a write-through; the dictionary is a cache.</summary>
    private void Put(LocalOutboxRecord record)
    {
        _records[record.MessageId] = record;
        _log?.Save(record);
    }

    /// <summary>The durable-accept point for a completed attempt.</summary>
    public LocalOutboxRecord Enqueue(ResultEnvelope result)
    {
        var errors = EnvelopeValidator.Validate(result);
        if (errors.Count > 0)
        {
            throw new InvalidOperationException(
                $"refusing to enqueue an invalid result: {string.Join("; ", errors.Select(e => $"{e.Field} {e.Reason}"))}");
        }

        var record = new LocalOutboxRecord(
            result.MessageId, result.IdempotencyKey, result,
            LocalOutboxState.Pending, 0, _clock(), _clock(), null, null);

        if (_records.TryGetValue(record.MessageId, out var held))
        {
            // Same message id: already durably held; the repeat is a no-op.
            return held;
        }

        Put(record);
        return record;
    }

    /// <summary>Attempts every due record once; returns how many got confirmed.</summary>
    public int Drain()
    {
        var now = _clock();
        var confirmed = 0;

        foreach (var record in _records.Values
                     .Where(r => r.State is LocalOutboxState.Pending or LocalOutboxState.Published
                                 && r.NextAttemptAt <= now)
                     .ToList())
        {
            var attempt = record with
            {
                State = LocalOutboxState.Published,
                AttemptCount = record.AttemptCount + 1,
                NextAttemptAt = null,
            };
            Put(attempt);

            if (_transport.Publish(attempt) == BrokerConfirm.Confirmed)
            {
                Put(attempt with
                {
                    State = LocalOutboxState.Confirmed,
                    ConfirmedAt = now,
                });
                confirmed++;
                continue;
            }

            if (attempt.AttemptCount >= _maxAttempts)
            {
                Put(attempt with
                {
                    State = LocalOutboxState.DeadLettered,
                    LastError = "publish not confirmed; retry budget exhausted",
                });
                continue;
            }

            var exponent = Math.Min(attempt.AttemptCount - 1, 20);
            var delay = TimeSpan.FromTicks(Math.Min(_baseDelay.Ticks * (1L << exponent), _maxDelay.Ticks));
            Put(attempt with
            {
                State = LocalOutboxState.Pending,
                NextAttemptAt = now + delay,
                LastError = "publish not confirmed",
            });
        }

        return confirmed;
    }

    /// <summary>
    /// Results the Node still owes the Server after a restart. Everything
    /// not confirmed is re-published under the same message and idempotency
    /// keys, so the Server dedups rather than double-counting (doc 150 PR-007).
    /// </summary>
    public IReadOnlyList<LocalOutboxRecord> UnackedAfterRestart() =>
        _records.Values
            .Where(r => r.State is LocalOutboxState.Pending or LocalOutboxState.Published)
            .ToList();
}

/// <summary>
/// Small helper for minting local detail event ids without pulling an SDK
/// (doc 151 §5.7 allows CloudEvents-compatible semantics, not a specific lib).
/// </summary>
public static class LocalEvents
{
    public static EventEnvelope For(string workerId, string attemptId, string eventType,
        string correlationId, string data, DateTimeOffset now, string? causationId = null)
    {
        var digest = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(
            $"{workerId}|{attemptId}|{eventType}|{now:O}|{Guid.NewGuid()}")))[..32].ToLowerInvariant();

        return new EventEnvelope
        {
            EventId = $"evt-{digest}",
            Source = $"node://{workerId}/attempt/{attemptId}",
            EventType = eventType,
            SchemaVersion = "execution-event.v1",
            Time = now,
            Subject = attemptId,
            CorrelationId = correlationId,
            CausationId = causationId,
            Data = data,
        };
    }
}
