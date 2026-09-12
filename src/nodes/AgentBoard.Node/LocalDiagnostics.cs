// SPDX-License-Identifier: MIT
using System.Collections.Concurrent;

namespace AgentBoard.Node;

/// <summary>
/// In-process observability. Zero external dependencies (no Prometheus,
/// no OpenTelemetry). Exposes:
///   1. Per-event Interlocked counters for the hot paths that mattered
///      in past production incidents (claim outcomes, rabbit disconnects,
///      execution results). Operators tail /api/diag to see live values
///      without scraping an external metrics backend.
///   2. A bounded ring buffer of the last N WARNING/ERROR log entries
///      (WorkerState.LastError is too coarse — a single string — and is
///      overwritten on every error).
///   3. A few timestamp "last seen" fields that survive across the
///      WorkerState restart to help answer "when did the last claim
///      actually succeed" without joining the executions table.
///
/// Everything is process-local: a worker restart resets to zero. That is
/// the right scope for "is this node OK right now"; trends across
/// restarts belong in a separate store (which the project has not
/// committed to building, see review 2026-09-12).
/// </summary>
public sealed class LocalDiagnostics
{
    // ---- counters (Interlocked-only) -------------------------------------
    private long _messagesConsumed;
    private long _messagesAcked;
    private long _messagesNacked;
    private long _claimAttempts;
    private long _claimSuccesses;
    private long _claimAckDrop;       // ghost / new_token_required / 404
    private long _claimConflict;      // lease / work_failed
    private long _claimForbidden;
    private long _claimUnexpected;
    private long _executionStarted;
    private long _executionSucceeded;
    private long _executionFailed;
    private long _executionDegraded;
    private long _rabbitDisconnects;
    private long _rabbitReconnects;
    private long _sqliteBusyRetries;

    public long MessagesConsumed     => Interlocked.Read(ref _messagesConsumed);
    public long MessagesAcked         => Interlocked.Read(ref _messagesAcked);
    public long MessagesNacked        => Interlocked.Read(ref _messagesNacked);
    public long ClaimAttempts         => Interlocked.Read(ref _claimAttempts);
    public long ClaimSuccesses        => Interlocked.Read(ref _claimSuccesses);
    public long ClaimAckDrop          => Interlocked.Read(ref _claimAckDrop);
    public long ClaimConflict         => Interlocked.Read(ref _claimConflict);
    public long ClaimForbidden        => Interlocked.Read(ref _claimForbidden);
    public long ClaimUnexpected       => Interlocked.Read(ref _claimUnexpected);
    public long ExecutionStarted      => Interlocked.Read(ref _executionStarted);
    public long ExecutionSucceeded    => Interlocked.Read(ref _executionSucceeded);
    public long ExecutionFailed       => Interlocked.Read(ref _executionFailed);
    public long ExecutionDegraded     => Interlocked.Read(ref _executionDegraded);
    public long RabbitDisconnects     => Interlocked.Read(ref _rabbitDisconnects);
    public long RabbitReconnects      => Interlocked.Read(ref _rabbitReconnects);
    public long SqliteBusyRetries     => Interlocked.Read(ref _sqliteBusyRetries);

    public void IncMessagesConsumed()  => Interlocked.Increment(ref _messagesConsumed);
    public void IncMessagesAcked()     => Interlocked.Increment(ref _messagesAcked);
    public void IncMessagesNacked()    => Interlocked.Increment(ref _messagesNacked);
    public void IncClaimAttempt()      => Interlocked.Increment(ref _claimAttempts);
    public void IncClaimSuccess()      => Interlocked.Increment(ref _claimSuccesses);
    public void IncClaimAckDrop()      => Interlocked.Increment(ref _claimAckDrop);
    public void IncClaimConflict()     => Interlocked.Increment(ref _claimConflict);
    public void IncClaimForbidden()    => Interlocked.Increment(ref _claimForbidden);
    public void IncClaimUnexpected()   => Interlocked.Increment(ref _claimUnexpected);
    public void IncExecutionStarted()  => Interlocked.Increment(ref _executionStarted);
    public void IncExecutionSucceeded()=> Interlocked.Increment(ref _executionSucceeded);
    public void IncExecutionFailed()   => Interlocked.Increment(ref _executionFailed);
    public void IncExecutionDegraded() => Interlocked.Increment(ref _executionDegraded);
    public void IncRabbitDisconnect()  => Interlocked.Increment(ref _rabbitDisconnects);
    public void IncRabbitReconnect()   => Interlocked.Increment(ref _rabbitReconnects);
    public void IncSqliteBusyRetry()   => Interlocked.Increment(ref _sqliteBusyRetries);

    // ---- last-seen timestamps (DateTimeOffset so consumers can format) ---
    public DateTimeOffset? LastClaimSuccessAt { get; set; }
    public DateTimeOffset? LastClaimFailureAt { get; set; }
    public DateTimeOffset? LastExecutionFinishAt { get; set; }
    public DateTimeOffset? LastRabbitDisconnectAt { get; set; }

    // ---- bounded ring buffer of recent warnings / errors -----------------
    public const int MaxRecentErrors = 50;
    private readonly ConcurrentQueue<ErrorEntry> _errors = new();

    public void RecordError(string level, string message, Exception? ex = null)
    {
        var entry = new ErrorEntry(
            At: DateTimeOffset.UtcNow,
            Level: level ?? "Information",
            Message: message ?? string.Empty,
            ExceptionType: ex is null ? null : ex.GetType().FullName);
        _errors.Enqueue(entry);
        // Trim: ConcurrentQueue has no Trim, so dequeue the oldest when we
        // exceed the cap. Done in a single-thread hot path to keep the
        // implementation simple; the queue length oscillates between
        // MaxRecentErrors and MaxRecentErrors+1 around the trim point.
        while (_errors.Count > MaxRecentErrors && _errors.TryDequeue(out _)) { }
    }

    public IReadOnlyList<ErrorEntry> RecentErrors(int limit = 50)
    {
        if (limit <= 0) limit = MaxRecentErrors;
        if (limit > MaxRecentErrors) limit = MaxRecentErrors;
        // Newest first; ConcurrentQueue yields oldest first so we reverse.
        var arr = _errors.ToArray();
        var n = Math.Min(arr.Length, limit);
        var result = new ErrorEntry[n];
        for (int i = 0; i < n; i++) result[i] = arr[arr.Length - 1 - i];
        return result;
    }
}

public readonly record struct ErrorEntry(
    DateTimeOffset At,
    string Level,
    string Message,
    string? ExceptionType);
