namespace AgentBoard.ProposalWorker;

public sealed class ProposalExecutionService
{
    private readonly WorkerState _state; private readonly ExecutionStore _store; private readonly WorkBuddyRunner _runner; private readonly ILogger<ProposalExecutionService> _log;
    public ProposalExecutionService(WorkerState state, ExecutionStore store, WorkBuddyRunner runner, ILogger<ProposalExecutionService> log) => (_state, _store, _runner, _log) = (state, store, runner, log);
    public async Task<bool> ExecuteAsync(ProposalMessage message, string source, CancellationToken ct)
    {
        if (_state.Paused) return false;
        _state.Begin($"proposal:{message.ProposalId}/round:{message.Round}"); long id = await _store.StartAsync(message, source, ct);
        try { var result = await _runner.RunAsync(message, ct); await _store.CompleteAsync(id, result.ExitCode, result.Output, result.Error, ct); if (result.ExitCode != 0) _state.LastError = result.Error; return result.ExitCode == 0; }
        finally { _state.End(); }
    }
}
