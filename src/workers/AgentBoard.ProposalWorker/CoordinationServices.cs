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
    private readonly RabbitMqOptions? _rabbit;
    private readonly ILogger<WorkerStartupService> _log;
    private readonly IAgentAdapterRegistry _registry;

    // 本地缓存：tool name -> AgentInstance id + 该 agent 的 bearer token
    // （P0-1：instance heartbeat 必须用注册时同一个身份，否则 401/403）
    private readonly Dictionary<string, (long InstanceId, string Token)> _instances = new();

    /// <summary>
    /// P0-3 fail-fast 的可观察面：ExecuteAsync 抛出的致命配置错误会先记录到
    /// 这里再 rethrow。生产上 Generic Host 默认 StopHost（进程退出即 fail
    /// fast）；.NET 10 的 BackgroundService.StartAsync 会吞掉同步异常，单测
    /// 用这个属性断言，不依赖 Host 管道。
    /// </summary>
    internal Exception? StartupFailure { get; private set; }

    public WorkerStartupService(
        IHttpClientFactory http,
        IOptions<WorkerOptions> worker,
        IOptions<AgentBoardOptions> agentboard,
        IOptions<AgentsOptions> agents,
        IAgentAdapterRegistry registry,
        ILogger<WorkerStartupService> log,
        IOptions<RabbitMqOptions>? rabbit = null)
    {
        _http = http;
        _worker = worker.Value;
        _agentboard = agentboard.Value;
        _agents = agents.Value;
        _rabbit = rabbit?.Value;
        _registry = registry;
        _log = log;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        try
        {
            await RunStartupAsync(stoppingToken);
        }
        catch (OperationCanceledException) { /* 正常退出 */ }
        catch (Exception ex)
        {
            StartupFailure = ex;
            throw;  // Generic Host 默认 StopHost → 进程退出 = fail fast
        }
    }

    private async Task RunStartupAsync(CancellationToken stoppingToken)
    {
        if (string.IsNullOrWhiteSpace(_agentboard.ServerUrl))
        {
            if (_agentboard.RequireRegistration)
            {
                // P0-3 fail-fast：生产要求注册但没给 ServerUrl，静默跳过只会让
                // scheduler 看不到任何 agent（用户侧表现为 Story 永远 todo）。
                throw new InvalidOperationException(
                    "AgentBoard:RequireRegistration=true but AgentBoard:ServerUrl is empty. " +
                    "Set AgentBoard:ServerUrl (e.g. http://<server>:58124) in appsettings.Production.json.");
            }
            _log.LogInformation(
                "PR-12 WorkerStartupService: AgentBoardOptions.ServerUrl 空，跳过 startup 注册（向后兼容）");
            return;
        }
        if (_agentboard.RequireRegistration && string.IsNullOrWhiteSpace(_rabbit?.Uri))
        {
            throw new InvalidOperationException(
                "AgentBoard:RequireRegistration=true but RabbitMq:Uri is empty. " +
                "The workflow consumer cannot receive task.assigned without a broker.");
        }
        // 1. register Worker（fastapi 端 upsert）
        await RegisterWorkerAsync(stoppingToken);
        // 2. 给每个启用的 agent upsert instance
        await UpsertAgentInstancesAsync(stoppingToken);
        // 3. P0-3 fail-fast：生产模式下至少要有 WorkBuddy 或 Codex 注册成功，
        //    否则 dispatch 永远选不到执行者（最常见的踩法是 Command 留空指望
        //    CliLocator 自动发现——scheduling 层根本看不到这个 agent）。
        if (_agentboard.RequireRegistration)
        {
            var registeredTools = _instances.Keys
                .Where(t => t is "workbuddy" or "codex" or "MiniMax" or "qwen")
                .ToList();
            if (registeredTools.Count == 0)
            {
                var detail = _agents.WorkBuddy.Command is null or ""
                    ? " Agents:WorkBuddy:Command is empty"
                    : "";
                detail += _agents.Codex.Command is null or ""
                    ? " Agents:Codex:Command is empty"
                    : "";
                throw new InvalidOperationException(
                    "AgentBoard:RequireRegistration=true but no WorkBuddy/Codex agent was registered." +
                    detail +
                    " Set Agents:WorkBuddy:Command (e.g. codebuddy) / Agents:Codex:Command (e.g. codex)" +
                    " and AgentBoard:ServerUrl + AgentBoard:StartupToken.");
            }
        }
        // 4. 周期性 heartbeat（用 agent heartbeat 端点）
        if (_instances.Count > 0)
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
            ApplyAuth(client, _agentboard.StartupToken);
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
        // 遍历显式可注册的 agent slot。Scenario 是 Golden gate 专用的
        // in-process HTTP adapter；只有 Command=enabled 时才注册，不会进入生产。
        var slots = new (string Tool, AgentOptions Opt)[]
        {
            ("workbuddy", _agents.WorkBuddy),
            ("codex",     _agents.Codex),
            ("MiniMax",   _agents.MiniMax),
            ("qwen",      _agents.Qwen),
            ("scenario",  _agents.Scenario),
        };
        foreach (var (tool, opt) in slots)
        {
            if (string.Equals(tool, "scenario", StringComparison.OrdinalIgnoreCase)
                && !string.Equals(
                    opt.Command, "enabled", StringComparison.OrdinalIgnoreCase))
            {
                if (!string.IsNullOrWhiteSpace(opt.Command))
                {
                    _log.LogWarning(
                        "PR-12: skip scenario (Command must equal 'enabled')");
                }
                continue;
            }
            if (string.IsNullOrWhiteSpace(opt.Command))
            {
                _log.LogInformation("PR-12: skip {Tool} (Command 空)", tool);
                continue;
            }
            var agentId = string.IsNullOrWhiteSpace(opt.AgentId)
                ? $"{_worker.Id}-{tool}"           // 默认 = "{worker_id}-{tool}"
                : opt.AgentId;
            // P0-1：per-agent 身份。AgentBoardToken 优先，空则回退全局
            // StartupToken（旧行为）。Reviewer isolation 要求同一 worker 上的
            // 不同 agent 有不同 user_id，因此多 agent 部署必须给每个 agent
            // 配独立 token（各自 register 出来的服务账号）。
            var agentToken = string.IsNullOrWhiteSpace(opt.AgentBoardToken)
                ? _agentboard.StartupToken
                : opt.AgentBoardToken;
            var reportedCommand = string.Equals(
                tool, "scenario", StringComparison.OrdinalIgnoreCase)
                ? ""
                : opt.Command;
            // workload（design/dev/review/qa）不是 Agent 永久角色；执行器类型
            // 单独写 AgentInstance.executor_type。roles 保留空数组兼容旧字段。
            var roles = Array.Empty<string>();
            try
            {
                var client = _http.CreateClient();
                ApplyAuth(client, agentToken);
                // 1) upsert agent 本身（idempotent）— PR-12 follow-up 改
                // POST /api/agents/register（之前 PUT /api/agents/{id} 在
                // fresh DB 上 404，因为 PUT 是 update-only）。
                var agentUrl = _agentboard.ServerUrl.TrimEnd('/') + "/api/agents/register";
                var agentPayload = new
                {
                    agent_id = agentId,
                    name = $"{tool} on {_worker.Id}",
                    roles = System.Text.Json.JsonSerializer.Serialize(roles),
                    cli_command = reportedCommand,
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
                    cli_command = reportedCommand,
                    model = "",
                    executor_type = tool.ToLowerInvariant(),
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
                    _instances[tool] = (idVal, agentToken);
                    _log.LogInformation(
                        "PR-12: agent {Agent} (tool={Tool}) instance {Id} registered (identity={Identity})",
                        agentId, tool, idVal,
                        string.IsNullOrWhiteSpace(opt.AgentBoardToken) ? "shared-startup-token" : "per-agent-token");
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
        if (_instances.Count == 0) return;
        try
        {
            foreach (var (tool, entry) in _instances)
            {
                var client = _http.CreateClient();
                // P0-1：heartbeat 用注册时的同一身份，服务端按 user 校验
                ApplyAuth(client, entry.Token);
                var url = _agentboard.ServerUrl.TrimEnd('/') +
                    $"/api/workers/{Uri.EscapeDataString(_worker.Id)}" +
                    $"/agent-instances/{entry.InstanceId}/heartbeat";
                var resp = await client.PostAsJsonAsync(url,
                    new { probe_ok = true, probe_message = "PR-12 startup heartbeat" }, ct);
                if (!resp.IsSuccessStatusCode)
                {
                    _log.LogDebug("PR-12: heartbeat instance {Id} HTTP {Status}",
                                   entry.InstanceId, resp.StatusCode);
                }
            }
        }
        catch (Exception ex) when (!ct.IsCancellationRequested)
        {
            _log.LogDebug(ex, "PR-12: heartbeat batch failed");
        }
    }

    private void ApplyAuth(HttpClient client, string token)
    {
        // 清空旧 header 再设，避免重试累积
        client.DefaultRequestHeaders.Remove("Authorization");
        if (!string.IsNullOrWhiteSpace(token))
        {
            client.DefaultRequestHeaders.Authorization =
                new System.Net.Http.Headers.AuthenticationHeaderValue(
                    "Bearer", token);
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
