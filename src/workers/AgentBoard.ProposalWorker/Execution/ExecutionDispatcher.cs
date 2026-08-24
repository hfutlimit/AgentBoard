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
            await foreach (var flight in _channel.Reader.ReadAllAsync(stoppingToken))
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
        catch (OperationCanceledException) { /* shutdown */ }
        _log.LogInformation("ExecutionDispatcher stopped");
    }
}
