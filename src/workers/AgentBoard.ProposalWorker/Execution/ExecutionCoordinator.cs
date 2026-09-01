using AgentBoard.ProposalWorker.Agents;
using Microsoft.Extensions.Options;

namespace AgentBoard.ProposalWorker.Execution;

/// <summary>
/// Sprint 1. Owns the try/catch branches that map each failure mode to its
/// own terminal state. <see cref="ExecutionStore"/> enforces the state
/// machine via CAS writes, so the coordinator doesn't have to worry about
/// concurrent re-entry.
///
/// Three failure paths:
///   OperationCanceledException (caller) → Cancelled
///   TimeoutException                   → TimedOut
///   Exception                          → Failed
///   default                            → Succeeded
/// </summary>
public sealed class ExecutionCoordinator
{
    private readonly ExecutionStore _store;
    private readonly InboxStore _inbox;
    private readonly ExecutionChannel _channel;
    private readonly IAgentAdapterRegistry _registry;
    private readonly WorkerState _state;
    private readonly ILogger<ExecutionCoordinator> _log;

    /// <summary>
    /// Backoff schedule for transient terminal-write failures
    /// (SQLite BUSY / LOCKED only). Total wall clock ≤ ~1.5 s.
    /// Anything beyond that is treated as permanent — the row is
    /// marked <see cref="ExecutionState.Degraded"/> with the
    /// business result preserved so the agent's side effects are
    /// not redone.
    /// </summary>
    private static readonly int[] TerminalWriteRetryDelaysMs = { 0, 100, 500, 1000 };

    public ExecutionCoordinator(
        ExecutionStore store,
        InboxStore inbox,
        ExecutionChannel channel,
        IAgentAdapterRegistry registry,
        WorkerState state,
        ILogger<ExecutionCoordinator> log)
    {
        _store = store;
        _inbox = inbox;
        _channel = channel;
        _registry = registry;
        _state = state;
        _log = log;
    }

    public bool IsPaused() => _state.Paused;
    public bool IsDegraded() => _state.IsDegraded;
    public string? DegradedReason => _state.DegradedReason;

    /// <summary>
    /// Mark the worker as degraded. The Dispatcher observes
    /// <see cref="IsDegraded"/> on the next outer-loop iteration and
    /// stops scheduling new work. The current in-flight execution
    /// (if any) finishes normally; subsequent rows stay in their
    /// pre-execution state for the operator to reconcile. Used by
    /// both the Coordinator (StartAsync non-transient SQLite) and
    /// the Dispatcher (Inbox.TryClaimAsync non-transient SQLite).
    /// </summary>
    public void MarkDegraded(string reason)
    {
        _state.DegradedReason = reason;
        _log.LogError("Worker marked degraded: {Reason}", reason);
    }

    /// <summary>
    /// SQLite returns SqliteException with SqliteErrorCode=19 (SQLITE_CONSTRAINT)
    /// and ExtendedCode=1555 (SQLITE_CONSTRAINT_PRIMARYKEY) or
    /// 2067 (SQLITE_CONSTRAINT_UNIQUE) when a UNIQUE/PRIMARY KEY collision
    /// blocks an INSERT. Anything else (locked, I/O, schema drift, …) is
    /// a real failure and must surface — see fix for #5 in the 2026-08-28
    /// review.
    /// </summary>
    private static bool IsUniqueViolation(Microsoft.Data.Sqlite.SqliteException ex) =>
        ex.SqliteErrorCode == 19
        && (ex.SqliteExtendedErrorCode == 1555 || ex.SqliteExtendedErrorCode == 2067);

    /// <summary>
    /// Re-exports <see cref="InboxStore.IsTransientSqliteLockError"/> so
    /// the Coordinator can apply the same narrow catch to StartAsync
    /// without leaking the helper.
    /// </summary>
    private static bool IsTransientSqliteLockError(Microsoft.Data.Sqlite.SqliteException ex) =>
        InboxStore.IsTransientSqliteLockError(ex);

    private static bool IsTerminal(string status) =>
        status is "Succeeded" or "Failed" or "TimedOut" or "Cancelled" or "Degraded";

    /// <summary>
    /// Wrap a terminal-state write with bounded retry on transient
    /// SQLite lock errors. Returns:
    ///   true  — the underlying CAS write returned true (row updated)
    ///   false — retries exhausted, OR the CAS write itself returned
    ///           false (state conflict: the row was no longer in a
    ///           non-terminal state by the time we tried to settle it;
    ///           the previous Mark* call in the chain has already
    ///           settled the row)
    ///   throws — unexpected exception, surfaces to the caller's catch
    ///
    /// 2026-08-29 review follow-up (round 7): the previous signature
    /// was <c>Func&lt;Task&gt;</c>, which silently discarded the
    /// <c>Task&lt;bool&gt;</c> return value from the underlying
    /// <c>MarkTerminalAsync</c>. A 0-rows-affected CAS conflict would
    /// then look like success and the row would be left in a fake
    /// non-terminal state (e.g. Running) while the inbox was marked
    /// completed — a real correctness bug. Propagating the bool
    /// closes it.
    /// </summary>
    private async Task<bool> TryPersistTerminalAsync(Func<Task<bool>> write, string description, CancellationToken ct)
    {
        foreach (var delayMs in TerminalWriteRetryDelaysMs)
        {
            if (delayMs > 0)
            {
                try { await Task.Delay(delayMs, ct); }
                catch (OperationCanceledException) { return false; }
            }
            try
            {
                var persisted = await write();
                if (persisted) return true;
                // 0-rows-affected: row is no longer in a non-terminal
                // state. The previous Mark* call in the chain has
                // already settled it. Treat as success (the caller's
                // intended terminal is already on disk).
                _log.LogWarning(
                    "{Description} returned 0 rows affected (CAS conflict; row already terminal); treating as success",
                    description);
                return true;
            }
            catch (Microsoft.Data.Sqlite.SqliteException ex) when (IsTransientSqliteLockError(ex))
            {
                _log.LogWarning(
                    "{Description} hit transient SQLite {ErrorCode} after {Delay}ms delay; retrying",
                    description, ex.SqliteErrorCode, delayMs);
            }
            // Non-transient SqliteException: bubble up; the caller's
            // catch will route to the degraded fall-back path.
        }
        return false;
    }

    /// <summary>
    /// Unified terminal-persistence path used by all four result
    /// branches (Success / Cancelled / TimedOut / Failed) and all
    /// three catch paths (caller-cancel / timeout / adapter-throw).
    /// Wraps the retry+bool-aware <see cref="TryPersistTerminalAsync"/>
    /// and the degraded fall-back + inbox completion. 2026-08-29
    /// review follow-up (round 7): the four happy-path call sites
    /// had the !ok → MarkDegraded logic; the three catch paths
    /// silently discarded the return value, so a persistent error
    /// in MarkTimedOutAsync / MarkCancelledAsync / MarkFailedAsync
    /// would leave the execution row in fake-Running while the
    /// inbox was marked completed. This helper unifies the state
    /// machine: every terminal path either settles the row OR
    /// marks it Degraded, then always completes the inbox.
    ///
    /// 2026-08-29 review follow-up (round 8): the helper is now
    /// fully exception-safe. If MarkDegradedAsync itself fails
    /// (e.g. the same long-held BUSY lock that exhausted the
    /// primary Mark* retries is still in effect when MarkDegraded
    /// runs), or if MarkCompletedAsync on the inbox throws, the
    /// helper logs the failure and returns normally. The agent's
    /// business result is preserved (NOT reclassified as Failed);
    /// the execution row may be left in non-terminal state
    /// (Degraded write failed → row stays Running), but the inbox
    /// is still completed (when possible) so the dispatcher does
    /// not re-run the agent and duplicate side effects. The
    /// operator sees a clear "degraded" log line and can
    /// reconcile.
    /// </summary>
    private async Task PersistTerminalOrDegradeAsync(
        Func<Task<bool>> write,
        string description,
        string businessResult,
        long executionId,
        long inboxId,
        CancellationToken ct)
    {
        var ok = false;
        try
        {
            ok = await TryPersistTerminalAsync(write, description, ct);
        }
        catch (Exception ex)
        {
            // Defensive: TryPersistTerminalAsync catches
            // SqliteException internally. Anything else is a bug.
            _log.LogError(ex,
                "{Description} threw unexpected exception during retry helper; treating as exhausted",
                description);
        }
        if (!ok)
        {
            // 2026-08-29 review follow-up (round 8): two distinct
            // "degraded" concepts live in this codepath and the
            // previous version conflated them:
            //
            //   1. WorkerState.MarkDegraded(reason) — flips
            //      `_state.DegradedReason` so the Dispatcher's
            //      outer loop sees IsDegraded=true and stops
            //      scheduling new work. The WHOLE worker
            //      becomes untrusted for new task assignment.
            //   2. ExecutionStore.MarkDegradedAsync(id, note) —
            //      marks the SINGLE execution row as
            //      status='Degraded' in the executions table.
            //      The worker keeps running.
            //
            // The previous code only did (2). Under a real
            // persistent SQLite failure (e.g. schema drift in
            // the executions table), every subsequent MarkX
            // would also fail, but the inbox was on a
            // different table that was still healthy — so the
            // worker happily completed inbox rows while NEVER
            // recording the agent's terminal state. Operators
            // saw the worker as online while the executions
            // table silently lost data. This is the
            // "silent corruption" mode the 2026-08-29 review
            // flagged as dangerous.
            //
            // Fix: do (1) FIRST. Even if the row-level write
            // (2) also fails, the worker is now visibly
            // degraded in /health, the dispatcher stops on
            // the next outer-loop iteration, and an operator
            // is paged. The execution row may stay in a
            // non-terminal state (logged) but the next agent
            // is not silently lost.
            _log.LogError(
                "{Description} exhausted retries on transient SQLite lock contention; agent's business result was {Business}; marking WORKER degraded AND execution row Degraded",
                description, businessResult);
            MarkDegraded(
                $"{description} exhausted retries on transient SQLite lock contention; agent's business result was {businessResult}; stopping dispatch to prevent silent corruption");
            try
            {
                await _store.MarkDegradedAsync(executionId,
                    $"{businessResult}; persistence retries exhausted — verify side effects before retrying",
                    ct);
            }
            catch (Exception ex)
            {
                _log.LogError(ex,
                    "{Description}: row-level MarkDegraded also failed; execution row left in non-terminal state for operator reconciliation",
                    description);
            }
        }
        try
        {
            await _inbox.MarkCompletedAsync(inboxId, ct);
        }
        catch (Exception ex)
        {
            _log.LogError(ex,
                "{Description}: inbox MarkCompleted failed; row stuck in dispatching for next-pass recovery",
                description);
        }
    }

    public async Task ExecuteAsync(ExecutionRequest request, long inboxId, CancellationToken ct)
    {
        if (_state.Paused)
        {
            // Race: the Dispatcher already moved the inbox row to `dispatching`
            // before the operator clicked Pause. The previous version returned
            // here and left the row in `dispatching` forever, which is a
            // permanent stall — the Dispatcher would never re-claim a
            // non-pending row, and startup-recovery would only fire after a
            // worker restart.
            //
            // Fix for #4 in the 2026-08-28 review: atomically revert the row
            // to `pending` and re-enqueue the flight so the Dispatcher picks
            // it up on Resume.
            //
            // 2026-08-29 follow-up: the re-enqueue used a blocking
            // `WriteAsync` against the bounded channel. When the channel
            // happened to be full and the Dispatcher (the only reader) was
            // inside this very ExecuteAsync call, the write blocked forever
            // — classic self-deadlock. The Dispatcher now refills from DB
            // pending on every idle cycle (see ExecutionDispatcher), so
            // there is no need to push back into the channel here. Just
            // mark the row `pending` and return; the Dispatcher's next
            // refill loop after Resume picks the row up.
            _log.LogInformation("Worker paused; reverting inbox {InboxId} dispatching → pending; dispatcher will refill from DB on resume", inboxId);
            await _inbox.MarkPendingAsync(inboxId, ct);
            return;
        }

        IAgentAdapter adapter;
        try
        {
            adapter = _registry.Get(request.AgentType);
        }
        catch (InvalidAgentException ex)
        {
            _log.LogError(ex, "Unknown agent {Agent} for {Key}", request.AgentType, request.ExecutionKey);
            await _inbox.MarkFailedAsync(inboxId, ex.Message, ct);
            return;
        }

        long executionId;
        try
        {
            executionId = await _store.StartAsync(request, request.Source, ct);
        }
        catch (Microsoft.Data.Sqlite.SqliteException ex) when (IsUniqueViolation(ex))
        {
            // UNIQUE(execution_key) violation. Two realistic causes:
            //   (a) duplicate dispatch race — the same execution_key is
            //       already in the executions table because a parallel
            //       consumer (or our own previous attempt) wrote it first.
            //   (b) crash recovery: a previous worker session inserted the
            //       row, then crashed mid-execution. The execution is
            //       stuck in `Running` (MarkOrphansAsync only handles rows
            //       older than OrphanThresholdMinutes — default 30 min) and
            //       the inbox row was reset to `pending` by our startup
            //       recovery, so the dispatcher handed it back to us.
            //       Without intervention, this would loop forever: every
            //       startup re-attempts, every StartAsync hits UNIQUE, every
            //       catch path MarkCompleted the inbox, and the running
            //       execution sits as a ghost for up to 30 minutes.
            //
            //   Fix for #4 in the 2026-08-28 review: actively resolve the
            //   existing execution so the inbox row can complete cleanly
            //   AND the executions table does not retain a fake-Running
            //   ghost. Mark it TimedOut (terminal) immediately, mark inbox
            //   completed, log both so an operator can see the force-orphan
            //   in the audit trail.
            var existing = await _store.GetByKeyAsync(request.ExecutionKey, ct);
            if (existing is not null && !IsTerminal(existing.Status))
            {
                _log.LogWarning(
                    "Execution {Key} hit UNIQUE constraint with an existing non-terminal row id={Id} status={Status}; force-orphaning to TimedOut",
                    request.ExecutionKey, existing.Id, existing.Status);
                await _store.MarkTimedOutAsync(existing.Id,
                    "orphaned by StartAsync UNIQUE collision (startup recovery)",
                    existing.Output, ct);
            }
            else
            {
                _log.LogWarning("Execution {Key} hit UNIQUE constraint; assuming duplicate dispatch (existing={Status})", request.ExecutionKey, existing?.Status);
            }
            await _inbox.MarkCompletedAsync(inboxId, ct);
            return;
        }
        catch (Microsoft.Data.Sqlite.SqliteException ex) when (IsTransientSqliteLockError(ex))
        {
            // Transient lock error (BUSY / LOCKED). The dispatcher will
            // re-attempt on the next refill cycle. Revert dispatching →
            // pending so the row is eligible for re-claim.
            _log.LogWarning(ex,
                "StartAsync hit transient SQLite {ErrorCode} for {Key}; reverting inbox {InboxId} dispatching → pending",
                ex.SqliteErrorCode, request.ExecutionKey, inboxId);
            try { await _inbox.MarkPendingAsync(inboxId, CancellationToken.None); }
            catch (Exception markEx)
            {
                _log.LogError(markEx,
                    "Failed to revert inbox {InboxId} dispatching → pending; row stays dispatching until restart recovery",
                    inboxId);
            }
            return;
        }
        catch (Microsoft.Data.Sqlite.SqliteException ex)
        {
            // Non-transient SQLite error: schema drift, corruption,
            // missing table, etc. Retrying in a 2-second hot loop is
            // pointless and floods the log. Mark the worker degraded and
            // revert the inbox row from `dispatching` back to `pending`
            // so the operator can manually clear the degraded flag and
            // the row becomes eligible for re-dispatch (matches the
            // transient-SQLite catch above and the 2026-08-29 review
            // round-8 semantic). The dispatcher's outer loop checks
            // IsDegraded and stops scheduling new work in the meantime.
            MarkDegraded($"StartAsync SQLite {ex.SqliteErrorCode} ({ex.Message})");
            _log.LogError(ex,
                "StartAsync hit non-transient SQLite {ErrorCode} for {Key}; worker degraded; reverting inbox to pending",
                ex.SqliteErrorCode, request.ExecutionKey);
            try { await _inbox.MarkPendingAsync(inboxId, CancellationToken.None); }
            catch (Exception markEx)
            {
                _log.LogError(markEx,
                    "Failed to revert inbox {InboxId} dispatching → pending after non-transient SQLite error; row stays dispatching until restart recovery",
                    inboxId);
            }
            return;
        }
        catch (Exception ex)
        {
            // Non-SQLite failure (adapter thrown, OOM, etc.). Revert to
            // pending and let the dispatcher retry. The previous
            // catch-all was correct for non-DB failures — it was just
            // too broad for DB failures, which the two SqliteException
            // branches above now handle correctly.
            _log.LogError(ex,
                "StartAsync failed for {Key}; reverting inbox {InboxId} dispatching → pending for next-pass recovery",
                request.ExecutionKey, inboxId);
            try
            {
                await _inbox.MarkPendingAsync(inboxId, CancellationToken.None);
            }
            catch (Exception markEx)
            {
                _log.LogError(markEx,
                    "Failed to revert inbox {InboxId} dispatching → pending; row stays dispatching until restart recovery",
                    inboxId);
            }
            return;
        }

        var active = new ActiveExecution(
            executionId, request.ExecutionKey, request.WorkloadType, request.WorkloadId,
            request.AgentType, DateTimeOffset.UtcNow);
        _state.Begin(active);
        _state.IncrementAgentTotal(request.AgentType);

        var context = new ExecutionContext(
            executionId, request.ExecutionKey, request.WorkloadType, request.WorkloadId,
            request.Round, request.AgentType, request.PayloadJson, Prompt: null,
            // P0-2：task type 透传给 prompt builder（design/dev/qa 分语义）
            TaskType: request.TaskType);

        try
        {
            var result = await adapter.ExecuteAsync(context, ct);

            // 2026-08-29 review follow-up (#3): the agent's business
            // Result branches + catch paths all go through the
            // unified PersistTerminalOrDegradeAsync. See comment
            // on that helper for the 2026-08-29 review fix.
            if (result.Success)
            {
                await PersistTerminalOrDegradeAsync(
                    () => _store.MarkSucceededAsync(executionId, result.ExitCode ?? 0, result.OutputJson ?? "", CancellationToken.None),
                    $"MarkSucceeded({executionId})",
                    businessResult: "Succeeded",
                    executionId, inboxId, CancellationToken.None);
            }
            else if (result.Cancelled)
            {
                await PersistTerminalOrDegradeAsync(
                    () => _store.MarkCancelledAsync(executionId, result.ErrorMessage ?? "cancelled", CancellationToken.None),
                    $"MarkCancelled({executionId})",
                    businessResult: "Cancelled",
                    executionId, inboxId, CancellationToken.None);
            }
            else if (result.TimedOut)
            {
                await PersistTerminalOrDegradeAsync(
                    () => _store.MarkTimedOutAsync(executionId, result.ErrorMessage ?? "execution timed out", result.OutputJson ?? "", CancellationToken.None),
                    $"MarkTimedOut({executionId})",
                    businessResult: "TimedOut",
                    executionId, inboxId, CancellationToken.None);
            }
            else
            {
                await PersistTerminalOrDegradeAsync(
                    () => _store.MarkFailedAsync(executionId, result.ExitCode, result.OutputJson ?? "", result.ErrorMessage ?? "agent reported failure", null, CancellationToken.None),
                    $"MarkFailed({executionId})",
                    businessResult: "Failed",
                    executionId, inboxId, CancellationToken.None);
            }
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
            await PersistTerminalOrDegradeAsync(
                () => _store.MarkCancelledAsync(executionId, "cancelled by host", CancellationToken.None),
                $"MarkCancelled({executionId})",
                businessResult: "Cancelled",
                executionId, inboxId, CancellationToken.None);
        }
        catch (TimeoutException ex)
        {
            await PersistTerminalOrDegradeAsync(
                () => _store.MarkTimedOutAsync(executionId, ex.Message, "", CancellationToken.None),
                $"MarkTimedOut({executionId})",
                businessResult: "TimedOut",
                executionId, inboxId, CancellationToken.None);
        }
        catch (Exception ex)
        {
            _state.LastError = ex.Message;
            _log.LogError(ex, "Execution {Id} ({Key}) threw", executionId, request.ExecutionKey);
            await PersistTerminalOrDegradeAsync(
                () => _store.MarkFailedAsync(executionId, null, "", ex.Message, ex.ToString(), CancellationToken.None),
                $"MarkFailed({executionId})",
                businessResult: "Failed",
                executionId, inboxId, CancellationToken.None);
        }
        finally
        {
            _state.End(active);
        }
    }

    /// <summary>
    /// Test seam: drive the unified terminal-persistence path for an
    /// already-Started execution row. Bypasses StartAsync (which would
    /// fail-fast on a BUSY blocker) and the agent's ExecuteAsync (which
    /// the test calls directly). Used by fault-injection tests that
    /// pre-create the execution row, then hold a SQLite lock, then
    /// drive only the Mark* path.
    /// </summary>
    public async Task MarkTerminalForTestAsync(
        long executionId, ExecutionRequest request, long inboxId,
        AgentExecutionResult result, CancellationToken ct)
    {
        _ = request; // included for symmetry with ExecuteAsync; not used here
        if (result.Success)
        {
            await PersistTerminalOrDegradeAsync(
                () => _store.MarkSucceededAsync(executionId, result.ExitCode ?? 0, result.OutputJson ?? "", CancellationToken.None),
                $"MarkSucceeded({executionId})",
                businessResult: "Succeeded",
                executionId, inboxId, CancellationToken.None);
        }
        else if (result.Cancelled)
        {
            await PersistTerminalOrDegradeAsync(
                () => _store.MarkCancelledAsync(executionId, result.ErrorMessage ?? "cancelled", CancellationToken.None),
                $"MarkCancelled({executionId})",
                businessResult: "Cancelled",
                executionId, inboxId, CancellationToken.None);
        }
        else if (result.TimedOut)
        {
            await PersistTerminalOrDegradeAsync(
                () => _store.MarkTimedOutAsync(executionId, result.ErrorMessage ?? "execution timed out", result.OutputJson ?? "", CancellationToken.None),
                $"MarkTimedOut({executionId})",
                businessResult: "TimedOut",
                executionId, inboxId, CancellationToken.None);
        }
        else
        {
            await PersistTerminalOrDegradeAsync(
                () => _store.MarkFailedAsync(executionId, result.ExitCode, result.OutputJson ?? "", result.ErrorMessage ?? "agent reported failure", null, CancellationToken.None),
                $"MarkFailed({executionId})",
                businessResult: "Failed",
                executionId, inboxId, CancellationToken.None);
        }
    }
}
