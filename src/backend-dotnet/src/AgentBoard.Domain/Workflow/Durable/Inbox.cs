// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Workflow.Durable;

/// <summary>Which of the two dedup dimensions a key belongs to (doc 151 §6.4).</summary>
public enum DedupKind
{
    /// <summary>Message-instance identity: catches broker redelivery of the same envelope.</summary>
    Message,

    /// <summary>Business-operation identity: catches two distinct messages performing one operation.</summary>
    Idempotency,
}

/// <summary>
/// A reservation, and — once processing finishes — the recorded result.
/// </summary>
/// <remarks>
/// doc 150 PR-007: "重复消息不会重复创建 StageRun、Outcome、artifact
/// registration 或 approval side effect." Returning the recorded outcome to a
/// redelivered message is what lets the caller answer the broker without
/// re-running the business effect.
/// </remarks>
public sealed record DedupEntry(
    string Key,
    DedupKind Kind,
    DateTimeOffset ReceivedAt,
    string? ProcessedOutcome);

/// <summary>
/// Server-side inbox/dedup store (doc 151 §5.6 step 1, §6.4).
/// </summary>
public sealed partial class Inbox
{
    private readonly Dictionary<string, DedupEntry> _entries = new(StringComparer.Ordinal);
    private readonly Func<DateTimeOffset> _clock;

    public Inbox(Func<DateTimeOffset> clock)
    {
        _clock = clock ?? throw new ArgumentNullException(nameof(clock));
    }

    public IReadOnlyCollection<DedupEntry> Entries => _entries.Values;

    public static string MessageKey(string messageId) => $"msg:{messageId}";
    public static string BusinessKey(string idempotencyKey) => $"idem:{idempotencyKey}";

    /// <summary>
    /// Reserves <paramref name="key"/> for processing. Returns false when the
    /// key was already seen; <paramref name="entry"/> then carries whatever was
    /// recorded, so the duplicate can be answered without side effects.
    /// </summary>
    public bool TryReserve(string key, DedupKind kind, out DedupEntry entry)
    {
        if (_entries.TryGetValue(key, out var existing))
        {
            entry = existing;
            return false;
        }

        var fresh = new DedupEntry(key, kind, _clock(), ProcessedOutcome: null);
        _entries[key] = fresh;
        entry = fresh;
        return true;
    }

    /// <summary>Records the outcome for a reserved key so later duplicates can replay it.</summary>
    public void Complete(string key, string processedOutcome)
    {
        if (!_entries.TryGetValue(key, out var existing))
        {
            throw new NotFoundException($"dedup key '{key}' was never reserved");
        }

        _entries[key] = existing with { ProcessedOutcome = processedOutcome };
    }

    /// <summary>
    /// Drops entries older than the cutoff. doc 151 §6.4: cleanup may only
    /// happen after the retry and audit windows have passed, so the cutoff is
    /// supplied by the operator configuration, never defaulted here.
    /// </summary>
    public int Prune(DateTimeOffset cutoff)
    {
        var stale = _entries.Values.Where(e => e.ReceivedAt < cutoff).Select(e => e.Key).ToList();
        foreach (var key in stale)
        {
            _entries.Remove(key);
        }

        return stale.Count;
    }
}
