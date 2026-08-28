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
            while (!stoppingToken.IsCancellationRequested)
            {
                if (_coordinator.IsPaused())
                {
                    await Task.Delay(TimeSpan.FromMilliseconds(200), stoppingToken);
                    continue;
                }
                if (!await _channel.Reader.WaitToReadAsync(stoppingToken)) break;
                while (_channel.Reader.TryRead(out var flight))
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
