using System.Collections.Concurrent;
using Microsoft.Extensions.Options;

namespace AgentBoard.ProposalWorker;

/// <summary>
/// Sprint 6. Per-agent state + capacity. The single worker tracks one
/// <c>running</c> counter and one <c>totalExecutions</c> counter per
/// registered agent so the heartbeat payload can describe all of them.
///
/// Multi-agent view:
///   {
///     worker_id, version, status, capacity, paused,
///     agents: { workbuddy: {running, total, lastUsedAt}, MiniMax: {...}, codex: {...} },
///     active_executions: [{id, agent_type, workload_type, workload_id, started_at}],
///     lastHeartbeatAttemptAt, lastHeartbeatSuccessAt, lastError
///   }
/// </summary>
public sealed class WorkerState
{
    private readonly object _gate = new();
    private bool _paused;
    private readonly ConcurrentDictionary<string, int> _runningByAgent = new();
    private readonly ConcurrentDictionary<string, long> _totalByAgent = new();
    private readonly ConcurrentDictionary<string, DateTimeOffset> _lastUsedByAgent = new();
    private readonly Dictionary<long, ActiveExecution> _active = new();

    public DateTimeOffset StartedAt { get; } = DateTimeOffset.UtcNow;
    public DateTimeOffset LastHeartbeatAttemptAt { get; set; } = DateTimeOffset.MinValue;
    public DateTimeOffset LastHeartbeatSuccessAt { get; set; } = DateTimeOffset.MinValue;
    public string? LastError { get; set; }

    public bool Paused { get { lock (_gate) return _paused; } set { lock (_gate) _paused = value; } }
    public string Version { get; }

    public WorkerState(IOptions<WorkerOptions> options) => Version = options.Value.Version;

    public void Begin(ActiveExecution exec)
    {
        lock (_gate) { _active[exec.ExecutionId] = exec; }
        _runningByAgent.AddOrUpdate(exec.AgentType, 1, (_, v) => v + 1);
        _lastUsedByAgent[exec.AgentType] = DateTimeOffset.UtcNow;
    }

    public void End(ActiveExecution exec)
    {
        lock (_gate) { _active.Remove(exec.ExecutionId); }
        _runningByAgent.AddOrUpdate(exec.AgentType, 0, (_, v) => Math.Max(0, v - 1));
    }

    public void IncrementAgentTotal(string agentType) =>
        _totalByAgent.AddOrUpdate(agentType, 1, (_, v) => v + 1);

    /// <summary>Snapshot consumed by /health, /api/worker, and heartbeat payload.</summary>
    public object Snapshot(IReadOnlyCollection<string> registeredAgents, int maxConcurrency, int running, int queued)
    {
        var agents = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
        foreach (var a in registeredAgents)
        {
            agents[a] = new
            {
                registered = true,
                running = _runningByAgent.GetValueOrDefault(a),
                total_executions = _totalByAgent.GetValueOrDefault(a),
                last_used_at = _lastUsedByAgent.TryGetValue(a, out var ts) ? ts : (DateTimeOffset?)null,
            };
        }
        ActiveExecution[] active;
        lock (_gate) active = _active.Values.OrderBy(x => x.StartedAt).ToArray();

        return new
        {
            worker_id = "",  // filled by caller (WorkerOptions.Id)
            version = Version,
            status = _paused ? "paused" : (running > 0 ? "busy" : "online"),
            capacity = new { max_concurrency = maxConcurrency, running, queued },
            paused = _paused,
            agents,
            active_executions = active.Select(a => new
            {
                execution_id = a.ExecutionId,
                execution_key = a.ExecutionKey,
                agent_type = a.AgentType,
                workload_type = a.WorkloadType,
                workload_id = a.WorkloadId,
                started_at = a.StartedAt,
            }).ToArray(),
            last_heartbeat_attempt_at = LastHeartbeatAttemptAt,
            last_heartbeat_success_at = LastHeartbeatSuccessAt,
            last_error = LastError,
        };
    }

    public int ActiveCount { get { lock (_gate) return _active.Count; } }
}
