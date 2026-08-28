namespace AgentBoard.ProposalWorker.Execution;

/// <summary>
/// Sprint 3. Background task that drains the in-memory dispatch channel and
/// hands each request to <see cref="ExecutionCoordinator"/>. One exception in
/// one execution must NOT crash the channel loop — caught and logged inline.
/// </summary>
public sealed class ExecutionDispatcher : BackgroundService
{
    private readonly ExecutionChannel _channel;
    private readonly InboxStore _inbox;
    private readonly ExecutionCoordinator _coordinator;
    private readonly ILogger<ExecutionDispatcher> _log;

    public ExecutionDispatcher(ExecutionChannel channel, InboxStore inbox, ExecutionCoordinator coordinator, ILogger<ExecutionDispatcher> log)
    {
        _channel = channel;
        _inbox = inbox;
        _coordinator = coordinator;
        _log = log;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        _log.LogInformation("ExecutionDispatcher started");

        // Startup recovery #2 in the 2026-08-28 review: a crash between
        // TryEnqueueAsync and Channel.Writer.WriteAsync previously left a
        // row stuck in `pending` forever. We now drain the durable inbox
        // here, after the dispatcher is the live consumer — the previous
        // version did this synchronously in Program.cs before the hosted
        // services were started, which deadlocked when the bounded
        // channel (capacity 100) was full and there was no consumer.
        try
        {
            var pending = await _inbox.ListPendingAsync(stoppingToken);
            if (pending.Count > 0)
            {
                _log.LogWarning(
                    "Recovered {Count} pending inbox rows from previous run; pushing into the dispatch channel",
                    pending.Count);
                foreach (var flight in pending)
                {
                    // TryWrite never blocks (returns false if the channel is
                    // full); if so we keep the row in the DB as `pending` and
                    // the next round of ListPendingAsync will pick it up after
                    // the current batch drains. The bounded channel acts as
                    // memory-pressure backpressure, not a hard cap.
                    if (!_channel.Writer.TryWrite(flight))
                    {
                        _log.LogWarning(
                            "Dispatch channel saturated while re-enqueuing {Key}; the row stays in DB `pending` and will be retried after the current batch completes",
                            flight.Request.ExecutionKey);
                        break;
                    }
                }
            }
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "Startup recovery from pending inbox failed; continuing with empty channel");
        }

        try
        {
            // Poll-and-skip-when-paused instead of an unconditional
            // `await foreach (ReadAllAsync)`. The previous version drained
            // the channel even while paused, which races with the Coordinator
            // (which re-enqueues a flight when it sees Paused) and produced a
            // tight re-enqueue → re-claim → re-enqueue loop until Pause was
            // cleared. Now the Dispatcher holds any in-flight items in the
            // channel buffer while paused, and the Coordinator's
            // `dispatching → pending` revert (plus its re-enqueue) only
            // matters for the narrow race where Pause is set after the
            // Dispatcher has already taken a flight. Fix for #4 in the
            // 2026-08-28 review.
            //
            // #3 follow-up: the inner drain loop also rechecks `IsPaused()`.
            // Without the recheck, an operator can flip Pause → false → true
            // between the outer check and the inner drain, and the inner
            // loop will keep claiming rows the Coordinator then reverts
            // (dispatching → pending → re-enqueue → claim → revert …) in a
            // tight CPU / SQLite UPDATE loop. Each iteration also pays a
            // MarkPendingAsync + WriteAsync round-trip, so it is not free.
            while (!stoppingToken.IsCancellationRequested)
            {
                if (_coordinator.IsPaused())
                {
                    await Task.Delay(TimeSpan.FromMilliseconds(200), stoppingToken);
                    continue;
                }
                if (!await _channel.Reader.WaitToReadAsync(stoppingToken)) break;
                while (!stoppingToken.IsCancellationRequested
                       && !_coordinator.IsPaused()
                       && _channel.Reader.TryRead(out var flight))
                {
                    try
                    {
                        // CAS: pending → dispatching. If we lose the race the row
                        // is already in flight from another consumer; skip.
                        if (!await _inbox.TryClaimAsync(flight.InboxId, stoppingToken))
                        {
                            _log.LogDebug("Inbox {InboxId} already claimed; skipping", flight.InboxId);
                            continue;
                        }
                        await _coordinator.ExecuteAsync(flight.Request, flight.InboxId, stoppingToken);
                    }
                    catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
                    {
                        break;
                    }
                    catch (Exception ex)
                    {
                        _log.LogError(ex, "Execution {Key} crashed in dispatcher", flight.Request.ExecutionKey);
                        try { await _inbox.MarkFailedAsync(flight.InboxId, ex.Message, CancellationToken.None); } catch { /* swallow */ }
                    }
                }
            }
        }
        catch (OperationCanceledException) { /* shutdown */ }
        _log.LogInformation("ExecutionDispatcher stopped");
    }
}
