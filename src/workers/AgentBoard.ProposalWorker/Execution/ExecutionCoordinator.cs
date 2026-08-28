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

    private static bool IsTerminal(string status) =>
        status is "Succeeded" or "Failed" or "TimedOut" or "Cancelled";

    public async Task ExecuteAsync(ExecutionRequest request, long inboxId, CancellationToken ct)
    {
        if (_state.Paused)
        {
            // Race: the Dispatcher already moved the inbox row to `dispatching`
            // before the operator clicked Pause. The previous version returned
            // here and left the row in `dispatching` forever, which is a
            // permanent stall — the Dispatcher would never re-claim a
            // non-pending row, and startup-recovery would only fire after a
            // worker restart. Fix for #4 in the 2026-08-28 review: atomically
            // revert the row to `pending` and re-enqueue the flight so the
            // Dispatcher picks it up on Resume.
            _log.LogInformation("Worker paused; reverting inbox {InboxId} dispatching → pending + re-enqueue", inboxId);
            await _inbox.MarkPendingAsync(inboxId, ct);
            await _channel.Writer.WriteAsync(new InFlightExecution(request, inboxId), ct);
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
        catch (Exception ex)
        {
            // Anything else (SQLite locked, I/O error, disk full, schema drift)
            // is a real failure: leave the inbox row in `dispatching` and let
            // the channel flush. The next startup recovery resets
            // `dispatching` to `pending` so we don't permanently lose the
            // message. Previously this catch swallowed all errors and
            // MarkCompleted the row, which silently dropped work on the
            // floor whenever the DB hiccupped (#5 in the 2026-08-28 review).
            _log.LogError(ex, "StartAsync failed for {Key}; leaving inbox {InboxId} in dispatching for next-pass recovery", request.ExecutionKey, inboxId);
            return;
        }

        var active = new ActiveExecution(
            executionId, request.ExecutionKey, request.WorkloadType, request.WorkloadId,
            request.AgentType, DateTimeOffset.UtcNow);
        _state.Begin(active);
        _state.IncrementAgentTotal(request.AgentType);

        var context = new ExecutionContext(
            executionId, request.ExecutionKey, request.WorkloadType, request.WorkloadId,
            request.Round, request.AgentType, request.PayloadJson, Prompt: null);

        try
        {
            var result = await adapter.ExecuteAsync(context, ct);
            if (result.Success)
            {
                await _store.MarkSucceededAsync(executionId, result.ExitCode ?? 0, result.OutputJson ?? "", ct);
                await _inbox.MarkCompletedAsync(inboxId, ct);
            }
            else if (result.Cancelled)
            {
                await _store.MarkCancelledAsync(executionId, result.ErrorMessage ?? "cancelled", ct);
                await _inbox.MarkCompletedAsync(inboxId, ct);
            }
            else if (result.TimedOut)
            {
                await _store.MarkTimedOutAsync(executionId, result.ErrorMessage ?? "execution timed out", result.OutputJson ?? "", ct);
                await _inbox.MarkCompletedAsync(inboxId, ct);
            }
            else
            {
                await _store.MarkFailedAsync(executionId, result.ExitCode, result.OutputJson ?? "", result.ErrorMessage ?? "agent reported failure", null, ct);
                await _inbox.MarkCompletedAsync(inboxId, ct);
            }
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
            await _store.MarkCancelledAsync(executionId, "cancelled by host", CancellationToken.None);
            await _inbox.MarkCompletedAsync(inboxId, CancellationToken.None);
        }
        catch (TimeoutException ex)
        {
            await _store.MarkTimedOutAsync(executionId, ex.Message, "", CancellationToken.None);
            await _inbox.MarkCompletedAsync(inboxId, CancellationToken.None);
        }
        catch (Exception ex)
        {
            _state.LastError = ex.Message;
            _log.LogError(ex, "Execution {Id} ({Key}) threw", executionId, request.ExecutionKey);
            await _store.MarkFailedAsync(executionId, null, "", ex.Message, ex.ToString(), CancellationToken.None);
            await _inbox.MarkCompletedAsync(inboxId, CancellationToken.None);
        }
        finally
        {
            _state.End(active);
        }
    }
}
