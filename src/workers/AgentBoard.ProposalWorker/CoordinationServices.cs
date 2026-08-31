using System.Net.WebSockets;
using System.Text;
using AgentBoard.ProposalWorker.Agents;
using Microsoft.Extensions.Options;

namespace AgentBoard.ProposalWorker;

public sealed class WorkerHeartbeatService : BackgroundService
{
    private readonly IHttpClientFactory _http;
    private readonly WorkerState _state;
    private readonly WorkerOptions _worker;
    private readonly WorkerIdentity _identity;
    private readonly AgentBoardOptions _agentBoard;
    private readonly IAgentAdapterRegistry _registry;
    private readonly WorkerOptions _opts;
    private readonly ILogger<WorkerHeartbeatService> _log;

    public WorkerHeartbeatService(
        IHttpClientFactory http,
        WorkerState state,
        IOptions<WorkerOptions> worker,
        WorkerIdentity identity,
        IOptions<AgentBoardOptions> agentBoard,
        IAgentAdapterRegistry registry,
        ILogger<WorkerHeartbeatService> log)
    {
        _http = http;
        _state = state;
        _worker = worker.Value;
        // Heartbeat must report the same resolved id that the server uses to
        // route work. Reading the raw config value here (the previous
        // behavior) caused the heartbeat to report "" while /health reported
        // the machine name (#7 in the 2026-08-28 review).
        _identity = identity;
        _agentBoard = agentBoard.Value;
        _registry = registry;
        _opts = worker.Value;
        _log = log;
    }

    protected override async Task ExecuteAsync(CancellationToken ct)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(Math.Max(5, _worker.HeartbeatSeconds)));
        do { await SendOnce(ct); } while (await timer.WaitForNextTickAsync(ct));
    }

    private async Task SendOnce(CancellationToken ct)
    {
        _state.LastHeartbeatAttemptAt = DateTimeOffset.UtcNow;
        if (string.IsNullOrWhiteSpace(_agentBoard.HeartbeatUrl)) return;
        try
        {
            var payload = _state.Snapshot(_registry.RegisteredAgents, _opts.MaxConcurrentExecutions, _state.ActiveCount, 0);
            var response = await _http.CreateClient().PostAsJsonAsync(_agentBoard.HeartbeatUrl, new
            {
                workerId = _identity.WorkerId,
                state = payload,
                timestamp = _state.LastHeartbeatAttemptAt,
            }, ct);
            if (response.IsSuccessStatusCode) _state.LastHeartbeatSuccessAt = DateTimeOffset.UtcNow;
            else _log.LogWarning("Worker heartbeat returned {Status}", response.StatusCode);
        }
        catch (Exception ex) when (!ct.IsCancellationRequested)
        {
            _log.LogWarning(ex, "Worker heartbeat failed");
        }
    }
}

// ===================== PR-12: Worker startup bootstrap =====================
//
// PR-2 review 收尾：.NET worker 启动时自动把本机 register 到 FastAPI
// 并为每个配置的 agent (WorkBuddy / Codex / MiniMax) upsert AgentInstance，
// 关闭"运维必须先手动建 worker + AgentInstance 行才能跑"这个配置缺口。
//
// 调用 FastAPI 端点（已存在）：
//   POST /api/workers/register
//   PUT  /api/agents/{agent_id}
//   POST /api/agents/{agent_id}/instances
//   POST /api/workers/{wid}/agent-instances/{id}/heartbeat  (周期性)
//
// retry / reconnect 暂不实现（PR-2 plan 明确先 happy path）—— 启动失败 log
// 错误，进程继续（避免配错就整个 worker 起不来）。后期再加重试。

public sealed class WorkerStartupService : BackgroundService
{
    private readonly IHttpClientFactory _http;
    private readonly WorkerOptions _worker;
    private readonly AgentBoardOptions _agentboard;
    private readonly AgentsOptions _agents;
    private readonly ILogger<WorkerStartupService> _log;
    private readonly IAgentAdapterRegistry _registry;

    // 本地缓存：tool name -> AgentInstance id（heartbeat 用）
    private readonly Dictionary<string, long> _instanceIds = new();

    public WorkerStartupService(
        IHttpClientFactory http,
        IOptions<WorkerOptions> worker,
        IOptions<AgentBoardOptions> agentboard,
        IOptions<AgentsOptions> agents,
        IAgentAdapterRegistry registry,
        ILogger<WorkerStartupService> log)
    {
        _http = http;
        _worker = worker.Value;
        _agentboard = agentboard.Value;
        _agents = agents.Value;
        _registry = registry;
        _log = log;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        if (string.IsNullOrWhiteSpace(_agentboard.ServerUrl))
        {
            _log.LogInformation(
                "PR-12 WorkerStartupService: AgentBoardOptions.ServerUrl 空，跳过 startup 注册（向后兼容）");
            return;
        }
        // 1. register Worker（fastapi 端 upsert）
        await RegisterWorkerAsync(stoppingToken);
        // 2. 给每个启用的 agent upsert instance
        await UpsertAgentInstancesAsync(stoppingToken);
        // 3. 周期性 heartbeat（用 agent heartbeat 端点）
        if (_instanceIds.Count > 0)
        {
            using var timer = new PeriodicTimer(TimeSpan.FromSeconds(
                Math.Max(5, _worker.HeartbeatSeconds)));
            try
            {
                while (await timer.WaitForNextTickAsync(stoppingToken))
                {
                    await HeartbeatAllAsync(stoppingToken);
                }
            }
            catch (OperationCanceledException) { /* 正常退出 */ }
        }
    }

    private async Task RegisterWorkerAsync(CancellationToken ct)
    {
        try
        {
            var client = _http.CreateClient();
            ApplyAuth(client);
            var url = _agentboard.ServerUrl.TrimEnd('/') + "/api/workers/register";
            var payload = new
            {
                worker_id = _worker.Id,
                hostname = Environment.MachineName,
                status = "active",
            };
            var resp = await client.PostAsJsonAsync(url, payload, ct);
            if (resp.IsSuccessStatusCode)
                _log.LogInformation("PR-12: Worker {Id} registered at {Url}", _worker.Id, url);
            else
                _log.LogWarning("PR-12: Worker register failed HTTP {Status}", resp.StatusCode);
        }
        catch (Exception ex) when (!ct.IsCancellationRequested)
        {
            _log.LogWarning(ex, "PR-12: Worker register failed");
        }
    }

    private async Task UpsertAgentInstancesAsync(CancellationToken ct)
    {
        // 遍历 4 个 agent slot：WorkBuddy / Codex / MiniMax / Fake
        // Fake 跳过（in-process 适配器，不需要 instance）
        var slots = new (string Tool, AgentOptions Opt)[]
        {
            ("workbuddy", _agents.WorkBuddy),
            ("codex",     _agents.Codex),
            ("MiniMax",   _agents.MiniMax),
        };
        foreach (var (tool, opt) in slots)
        {
            if (string.IsNullOrWhiteSpace(opt.Command))
            {
                _log.LogInformation("PR-12: skip {Tool} (Command 空)", tool);
                continue;
            }
            var agentId = string.IsNullOrWhiteSpace(opt.AgentId)
                ? $"{_worker.Id}-{tool}"           // 默认 = "{worker_id}-{tool}"
                : opt.AgentId;
            // PR-12 follow-up：WorkBuddy role 数组加 "reviewer" —
            // review scheduler（_online_reviewer_candidates）要求 roles 含
            // "reviewer" + online + project member。光 "workbuddy" 配出来
            // 不能被选为 reviewer，PR-13b 第一版 happy path 必卡这里。
            // Codex 不需要 reviewer role（不审 review）。
            var roles = tool == "workbuddy"
                ? new[] { "workbuddy", "reviewer" }
                : new[] { tool };
            try
            {
                var client = _http.CreateClient();
                ApplyAuth(client);
                // 1) upsert agent 本身（idempotent）— PR-12 follow-up 改
                // POST /api/agents/register（之前 PUT /api/agents/{id} 在
                // fresh DB 上 404，因为 PUT 是 update-only）。
                var agentUrl = _agentboard.ServerUrl.TrimEnd('/') + "/api/agents/register";
                var agentPayload = new
                {
                    agent_id = agentId,
                    name = $"{tool} on {_worker.Id}",
                    roles = System.Text.Json.JsonSerializer.Serialize(roles),
                    cli_command = opt.Command,
                    // model 字段 .NET AgentOptions 没有；保持空字符串
                    // (FastAPI AgentRegisterIn.model default = "")
                };
                var agentResp = await client.PostAsJsonAsync(agentUrl, agentPayload, ct);
                agentResp.EnsureSuccessStatusCode();
                // 2) upsert AgentInstance
                var instUrl = _agentboard.ServerUrl.TrimEnd('/') +
                    $"/api/agents/{Uri.EscapeDataString(agentId)}/instances";
                var instPayload = new
                {
                    worker_id = _worker.Id,
                    cli_command = opt.Command,
                    model = "",
                    auth_key = opt.ApiKeyEnv ?? "",
                    enabled = true,
                };
                var instResp = await client.PostAsJsonAsync(instUrl, instPayload, ct);
                if (!instResp.IsSuccessStatusCode)
                {
                    _log.LogWarning("PR-12: agent {Agent} instance create HTTP {Status}",
                                    agentId, instResp.StatusCode);
                    continue;
                }
                // 取 instance id（response 是 AgentInstance dict，含 id 字段）
                var instData = await instResp.Content.ReadFromJsonAsync<Dictionary<string, object>>(cancellationToken: ct);
                if (instData != null && instData.TryGetValue("id", out var idObj) &&
                    long.TryParse(idObj?.ToString(), out var idVal))
                {
                    _instanceIds[tool] = idVal;
                    _log.LogInformation(
                        "PR-12: agent {Agent} (tool={Tool}) instance {Id} registered",
                        agentId, tool, idVal);
                }
            }
            catch (Exception ex) when (!ct.IsCancellationRequested)
            {
                _log.LogWarning(ex, "PR-12: agent {Tool} upsert failed", tool);
            }
        }
    }

    private async Task HeartbeatAllAsync(CancellationToken ct)
    {
        if (_instanceIds.Count == 0) return;
        try
        {
            var client = _http.CreateClient();
            ApplyAuth(client);
            foreach (var (tool, instId) in _instanceIds)
            {
                var url = _agentboard.ServerUrl.TrimEnd('/') +
                    $"/api/workers/{Uri.EscapeDataString(_worker.Id)}" +
                    $"/agent-instances/{instId}/heartbeat";
                var resp = await client.PostAsJsonAsync(url,
                    new { probe_ok = true, probe_message = "PR-12 startup heartbeat" }, ct);
                if (!resp.IsSuccessStatusCode)
                {
                    _log.LogDebug("PR-12: heartbeat instance {Id} HTTP {Status}",
                                   instId, resp.StatusCode);
                }
            }
        }
        catch (Exception ex) when (!ct.IsCancellationRequested)
        {
            _log.LogDebug(ex, "PR-12: heartbeat batch failed");
        }
    }

    private void ApplyAuth(HttpClient client)
    {
        // 清空旧 header 再设，避免重试累积
        client.DefaultRequestHeaders.Remove("Authorization");
        if (!string.IsNullOrWhiteSpace(_agentboard.StartupToken))
        {
            client.DefaultRequestHeaders.Authorization =
                new System.Net.Http.Headers.AuthenticationHeaderValue(
                    "Bearer", _agentboard.StartupToken);
        }
    }
}

public sealed class AgentBoardWebSocketService : BackgroundService
{
    private readonly AgentBoardOptions _options;
    private readonly WorkerState _state;
    private readonly ILogger<AgentBoardWebSocketService> _log;

    public AgentBoardWebSocketService(IOptions<AgentBoardOptions> options, WorkerState state, ILogger<AgentBoardWebSocketService> log)
    {
        _options = options.Value;
        _state = state;
        _log = log;
    }

    protected override async Task ExecuteAsync(CancellationToken ct)
    {
        if (string.IsNullOrWhiteSpace(_options.WebSocketUrl)) return;
        var delay = TimeSpan.FromSeconds(1);
        while (!ct.IsCancellationRequested)
        {
            try
            {
                using var socket = new ClientWebSocket();
                await socket.ConnectAsync(new Uri(_options.WebSocketUrl), ct);
                delay = TimeSpan.FromSeconds(1);
                var bytes = new byte[4096];
                while (socket.State == WebSocketState.Open && !ct.IsCancellationRequested)
                {
                    var result = await socket.ReceiveAsync(bytes, ct);
                    if (result.MessageType == WebSocketMessageType.Close) break;
                    // Sprint 6 follow-up: parse event payload (cancel/pause/config)
                    // and dispatch. For now we only log.
                    _log.LogInformation("AgentBoard worker websocket event: {Event}", Encoding.UTF8.GetString(bytes, 0, result.Count));
                }
            }
            catch (Exception ex) when (!ct.IsCancellationRequested)
            {
                _state.LastError = ex.Message;
                _log.LogWarning(ex, "Worker websocket disconnected");
            }
            await Task.Delay(delay, ct);
            delay = TimeSpan.FromSeconds(Math.Min(30, delay.TotalSeconds * 2));
        }
    }
}
