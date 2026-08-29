using System.Collections.Concurrent;
using AgentBoard.ProposalWorker.Agents;
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
    /// <summary>
    /// Set to a non-null reason when a non-recoverable failure is
    /// observed (e.g. SQLite schema drift, corruption, missing
    /// table). The dispatcher stops scheduling new work; the
    /// <see cref="Snapshot"/> exposes the reason in the worker
    /// status so /health, the installer, and dashboards can alert.
    /// Operator must clear the flag (e.g. after fixing the DB) via
    /// a future /api/control/resume extension; the worker is not
    /// designed to auto-recover from corruption.
    /// </summary>
    private string? _degradedReason;
    private readonly ConcurrentDictionary<string, int> _runningByAgent = new();
    private readonly ConcurrentDictionary<string, long> _totalByAgent = new();
    private readonly ConcurrentDictionary<string, DateTimeOffset> _lastUsedByAgent = new();
    // Per-agent CLI readiness. Replaces the previous single-bool `ready`
    // gate (fix for #6 in the 2026-08-28 review): a binary can be present
    // while the credential is missing, and that must surface in /health
    // and the install script. The combined `ready` flag is honored by
    // the installer.
    private readonly ConcurrentDictionary<string, AgentReadiness> _readinessByAgent = new();
    private readonly Dictionary<long, ActiveExecution> _active = new();

    public DateTimeOffset StartedAt { get; } = DateTimeOffset.UtcNow;
    public DateTimeOffset LastHeartbeatAttemptAt { get; set; } = DateTimeOffset.MinValue;
    public DateTimeOffset LastHeartbeatSuccessAt { get; set; } = DateTimeOffset.MinValue;
    public string? LastError { get; set; }

    public bool Paused { get { lock (_gate) return _paused; } set { lock (_gate) _paused = value; } }
    public string? DegradedReason
    {
        get { lock (_gate) return _degradedReason; }
        set { lock (_gate) _degradedReason = value; }
    }
    public bool IsDegraded
    {
        get { lock (_gate) return _degradedReason is not null; }
    }
    public string Version { get; }
    public string WorkerId { get; }

    public WorkerState(IOptions<WorkerOptions> options, WorkerIdentity identity)
    {
        Version = options.Value.Version;
        // Always read the resolved worker id from the single source of truth
        // (WorkerIdentity). No fallback here — that lives in WorkerIdentity's
        // ctor so health, RabbitMQ, and heartbeat can never disagree (#7 in
        // the 2026-08-28 review).
        WorkerId = identity.WorkerId;
    }

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

    /// <summary>
    /// Replace the readiness report for an agent. Called by
    /// <see cref="Agents.ReadinessProbe"/> at startup; replaces the
    /// previous single-bool `ready` field (#5 / #6 in the 2026-08-28
    /// review).
    /// </summary>
    public void SetAgentReport(string agentType, AgentReadiness report) =>
        _readinessByAgent[agentType] = report;

    /// <summary>True iff every registered agent reported <c>ready=true</c>.</summary>
    public bool AllAgentsReady(IReadOnlyCollection<string> registeredAgents) =>
        registeredAgents.All(a => _readinessByAgent.TryGetValue(a, out var r) && r.Ready);

    /// <summary>Snapshot consumed by /health, /api/worker, and heartbeat payload.</summary>
    public object Snapshot(IReadOnlyCollection<string> registeredAgents, int maxConcurrency, int running, int queued)
    {
        var agents = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
        foreach (var a in registeredAgents)
        {
            var readiness = _readinessByAgent.TryGetValue(a, out var r)
                ? r
                : new AgentReadiness(false, "probe not run", false, "probe not run");
            agents[a] = new
            {
                registered = true,
                cli_ready = readiness.CliReady,
                cli_error = readiness.CliError,
                credential_ready = readiness.CredentialReady,
                credential_error = readiness.CredentialError,
                ready = readiness.Ready,
                ready_error = readiness.Ready ? null
                    : (readiness.CliReady ? readiness.CredentialError : readiness.CliError),
                running = _runningByAgent.GetValueOrDefault(a),
                total_executions = _totalByAgent.GetValueOrDefault(a),
                last_used_at = _lastUsedByAgent.TryGetValue(a, out var ts) ? ts : (DateTimeOffset?)null,
            };
        }
        ActiveExecution[] active;
        lock (_gate) active = _active.Values.OrderBy(x => x.StartedAt).ToArray();

        return new
        {
            worker_id = WorkerId,
            version = Version,
            status = _degradedReason is not null
                ? "degraded"
                : (_paused ? "paused" : (running > 0 ? "busy" : "online")),
            degraded_reason = _degradedReason,
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
