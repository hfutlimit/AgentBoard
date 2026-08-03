namespace AgentBoard.ProposalWorker;

/// <summary>Runs an operator-approved retry from local history without forging a new RabbitMQ payload.</summary>
public sealed class PortalRetryService : BackgroundService
{
    private readonly ExecutionStore _store; private readonly ProposalExecutionService _execution; private readonly WorkerState _state; private readonly ILogger<PortalRetryService> _log;
    public PortalRetryService(ExecutionStore store, ProposalExecutionService execution, WorkerState state, ILogger<PortalRetryService> log) => (_store, _execution, _state, _log) = (store, execution, state, log);
    protected override async Task ExecuteAsync(CancellationToken ct)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(2));
        while (await timer.WaitForNextTickAsync(ct))
        {
            if (_state.Paused || _state.Busy) continue;
            foreach (var retry in await _store.GetRetryRequestsAsync())
            {
                await _store.ClearRetryAsync(retry.Id);
                try { await _execution.ExecuteAsync(retry.Message, "portal-retry", ct); }
                catch (Exception ex) { _log.LogError(ex, "Portal retry failed before WorkBuddy could start"); }
                break;
            }
        }
    }
}
