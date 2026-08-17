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
    private readonly IAgentAdapterRegistry _registry;
    private readonly WorkerState _state;
    private readonly ILogger<ExecutionCoordinator> _log;

    public ExecutionCoordinator(ExecutionStore store, InboxStore inbox, IAgentAdapterRegistry registry, WorkerState state, ILogger<ExecutionCoordinator> log)
    {
        _store = store;
        _inbox = inbox;
        _registry = registry;
        _state = state;
        _log = log;
    }

    public async Task ExecuteAsync(ExecutionRequest request, long inboxId, CancellationToken ct)
    {
        if (_state.Paused)
        {
            _log.LogInformation("Worker paused; leaving inbox row {InboxId} as pending", inboxId);
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
        catch (Exception ex)
        {
            // UNIQUE(execution_key) violation is the only realistic case
            // (double-dispatch race). Treat as already-handled, mark inbox
            // completed so we don't loop.
            _log.LogWarning(ex, "Could not start execution for {Key}; assuming duplicate", request.ExecutionKey);
            await _inbox.MarkCompletedAsync(inboxId, ct);
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
