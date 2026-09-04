// SPDX-License-Identifier: MIT
using AgentBoard.Contracts;
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Workflow.Durable;

/// <summary>
/// Bounded exponential backoff parameters for one failure category
/// (doc 150 PR-012: "backoff 上限").
/// </summary>
public sealed record RetryPolicy(int MaxAttempts, TimeSpan BaseDelay, TimeSpan MaxDelay)
{
    public static RetryPolicy Default { get; } = new(
        MaxAttempts: 5,
        BaseDelay: TimeSpan.FromSeconds(2),
        MaxDelay: TimeSpan.FromMinutes(10));

    /// <summary>No retry budget: every failure goes straight to the DLQ.</summary>
    public static RetryPolicy Never { get; } = new(0, TimeSpan.Zero, TimeSpan.Zero);

    public TimeSpan DelayForFailure(int failureNumber)
    {
        if (failureNumber < 1)
        {
            throw new InvalidValueException("failure numbers start at 1");
        }

        // base * 2^(n-1), saturated at MaxDelay; the shift is clamped so a
        // long retry chain cannot overflow into a negative delay.
        var exponent = Math.Min(failureNumber - 1, 20);
        var raw = BaseDelay.Ticks * (1L << exponent);
        var capped = Math.Min(raw, MaxDelay.Ticks);
        return TimeSpan.FromTicks(capped);
    }
}

/// <summary>
/// What to do after the Nth failure of one category: schedule another attempt
/// or hand the work to an operator via the DLQ.
/// </summary>
public sealed record RetryDecision(bool IsRetry, TimeSpan? Delay, string Reason)
{
    public static RetryDecision RetryAfter(TimeSpan delay) =>
        new(true, delay, "within retry budget");

    public static RetryDecision DeadLetter(string reason) =>
        new(false, null, reason);
}

/// <summary>
/// Maps failure categories to bounded retry plans (doc 150 PR-012).
/// </summary>
/// <remarks>
/// The retryability question itself is answered by
/// <see cref="FailureCategories.IsRetryable"/>, frozen in A0 — this type only
/// adds the budget dimension, so the two cannot drift apart.
/// </remarks>
public sealed class RetryPlanner
{
    private readonly Func<FailureCategory, RetryPolicy> _policyFor;

    public RetryPlanner(Func<FailureCategory, RetryPolicy>? policyOverride = null)
    {
        _policyFor = policyOverride ?? (_ => RetryPolicy.Default);
    }

    public RetryDecision Decide(FailureCategory category, int failureNumber)
    {
        if (!FailureCategories.IsRetryable(category))
        {
            return RetryDecision.DeadLetter($"{category} is not retryable (doc 150 PR-012)");
        }

        var policy = _policyFor(category);

        if (failureNumber > policy.MaxAttempts)
        {
            return RetryDecision.DeadLetter(
                $"retry budget of {policy.MaxAttempts} exhausted for {category}");
        }

        return RetryDecision.RetryAfter(policy.DelayForFailure(failureNumber));
    }
}

public enum DeadLetterState
{
    /// <summary>Awaiting operator inspection.</summary>
    Quarantined,

    /// <summary>Operator replayed the underlying work.</summary>
    Replayed,

    /// <summary>Operator gave up; kept for audit only.</summary>
    Abandoned,
}

/// <summary>One quarantined unit of failed work (doc 151 §6.3, NFR-010).</summary>
public sealed record DeadLetterEntry(
    string Id,
    string? MessageId,
    string? ExecutionId,
    FailureCategory Category,
    string Reason,
    DateTimeOffset EnqueuedAt,
    DeadLetterState State = DeadLetterState.Quarantined,
    DateTimeOffset? DecidedAt = null,
    string? DecidedBy = null);

/// <summary>
/// The DLQ an operator inspects, replays, or abandons work from.
/// Nothing here is ever silently retried by the system.
/// </summary>
public sealed partial class DeadLetterQueue
{
    private readonly Dictionary<string, DeadLetterEntry> _entries = new(StringComparer.Ordinal);

    public IReadOnlyCollection<DeadLetterEntry> Entries => _entries.Values;

    public DeadLetterQueue()
    {
    }

    public DeadLetterEntry Enqueue(DeadLetterEntry entry)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(entry.Id);

        if (!_entries.TryAdd(entry.Id, entry))
        {
            throw new DuplicateException($"dead-letter entry '{entry.Id}' already exists");
        }

        return entry;
    }

    public IReadOnlyList<DeadLetterEntry> Quarantined() =>
        _entries.Values.Where(e => e.State == DeadLetterState.Quarantined).ToList();

    public DeadLetterEntry Require(string id) =>
        _entries.TryGetValue(id, out var entry)
            ? entry
            : throw new NotFoundException($"dead-letter entry '{id}' not found");

    public DeadLetterEntry Resolve(string id, DeadLetterState target, DateTimeOffset now, string actor)
    {
        var entry = Require(id);

        if (entry.State != DeadLetterState.Quarantined)
        {
            throw new InvalidValueException($"'{id}' is already {entry.State}; only quarantined entries can be resolved");
        }

        if (target == DeadLetterState.Quarantined)
        {
            throw new InvalidValueException("resolving must move the entry out of quarantine");
        }

        var resolved = entry with
        {
            State = target,
            DecidedAt = now,
            DecidedBy = actor,
        };

        _entries[id] = resolved;
        return resolved;
    }
}
