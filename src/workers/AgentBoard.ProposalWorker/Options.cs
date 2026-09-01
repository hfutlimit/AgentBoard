namespace AgentBoard.ProposalWorker;

// =============================================================================
// Core worker options
// =============================================================================

public sealed class WorkerOptions
{
    public string Id { get; set; } = Environment.MachineName;
    public int HeartbeatSeconds { get; set; } = 15;
    public string HistoryDatabasePath { get; set; } = "data\\proposal-worker.db";
    public int MaxConcurrentExecutions { get; set; } = 1;
    public int DispatchChannelCapacity { get; set; } = 100;
    public int OrphanThresholdMinutes { get; set; } = 30;
    /// <summary>
    /// 2026-08-29 round-8 review follow-up: hard cap on the
    /// local SQLite inbox row count. The RabbitMQ consumer
    /// requeues (or, for the direct queue, BasicCancels the
    /// consumer) when this watermark is exceeded, so a fast
    /// inbound rate can no longer grow the local DB without
    /// bound. Replaces the previous DropWrite-only strategy
    /// which traded channel-level blocking for unbounded disk
    /// growth + unfair ACK stealing in multi-worker deploys.
    /// Default 1000. Set to 0 to disable (not recommended).
    /// </summary>
    public int MaxPendingInbox { get; set; } = 1000;
    public string Version { get; set; } = "1.4.0";
}

public sealed class RabbitMqOptions
{
    public string Uri { get; set; } = "";
    public string Namespace { get; set; } = "agentboard.proposals";
    public string PublicRoutingKey { get; set; } = "dispatch";
    public string DirectExchangeSuffix { get; set; } = ".direct";
    public ushort Prefetch { get; set; } = 1;
    public string DirectExchange => Namespace + DirectExchangeSuffix;
    public string PublicQueue => Namespace + ".work";
    public string WorkerQueue(string workerId) => Namespace + ".worker." + workerId;
    public string WorkerRoutingKey(string workerId) => "worker." + workerId;
    // ---- Sprint 12: workflow event namespace -----------------------------
    // The .NET worker subscribes to the FastAPI WorkflowTopology
    // (agentboard.workflow topic exchange). The broadcast queue catches
    // every workflow.broadcast.* event; the agent queue catches events
    // addressed to this specific worker (workflow.agent.{workerId}).
    // Mirrors src/backend-fastapi/agentboard/core/infrastructure/
    // messaging/rabbitmq.py::WorkflowTopology.
    public string WorkflowNamespace { get; set; } = "agentboard.workflow";
    public string WorkflowBroadcastPattern { get; set; } = "workflow.broadcast.#";
    public string WorkflowAgentPattern => $"workflow.agent.";
    public string WorkflowBroadcastQueue => WorkflowNamespace + ".broadcast";
    public string WorkflowAgentQueue(string workerId) => WorkflowNamespace + ".agent." + workerId;
    public string WorkflowDlxExchange => WorkflowNamespace + ".dlx";
    public string WorkflowDeadQueue => WorkflowNamespace + ".dead";
    /// <summary>
    /// Master switch. When false, the worker does not subscribe to
    /// the workflow exchange at all (legacy proposal-only behavior).
    /// Default true because Sprint 12 closed the orchestration gap and
    /// the worker is now a Generic AgentWorker.
    /// </summary>
    public bool WorkflowConsumerEnabled { get; set; } = true;
}

// =============================================================================
// Sprint 4: Per-agent options. The single worker is configured to know all
// three (workbuddy / MiniMax / codex); each agent has its own CLI command
// and timeout. Setting Command = "" disables an agent at startup.
// =============================================================================

public sealed class AgentOptions
{
    public string Command { get; set; } = "";
    public string[] Arguments { get; set; } = Array.Empty<string>();
    public string WorkingDirectory { get; set; } = "";
    public int TimeoutMinutes { get; set; } = 30;
    public int MaxCapturedOutputChars { get; set; } = 20000;
    /// <summary>
    /// Optional deterministic delay used only by the in-process scenario
    /// adapter to make cross-worker orchestration checkpoints observable.
    /// Real CLI adapters ignore this value.
    /// </summary>
    public int DelayMilliseconds { get; set; }
    public string? ApiKeyEnv { get; set; }  // optional; injected only if set
    /// <summary>
    /// PR-12：logical agent identity（PR-11 WorkflowMessage.agent_id）。
    /// 多 agent 同 type 时靠这个区分（codex-dev-1 vs codex-dev-2）。
    /// startup 时 upsert 到 FastAPI /api/agents/{agent_id} + instance。
    /// 默认 = tools 简写（"workbuddy" / "codex" / "MiniMax"）。
    /// </summary>
    public string AgentId { get; set; } = "";
    /// <summary>
    /// P0-1 (2026-09-01)：per-agent FastAPI bearer token。设置后本 agent 的
    /// register / instance / heartbeat 都以这个身份调用，使同一台 worker 上的
    /// 多个 agent 拿到不同的 user_id —— reviewer isolation
    /// （assign_task_reviewer 排除 a.user_id == task.assignee_id）依赖这一点，
    /// 否则单机 WorkBuddy + Codex 会因为同 user 互相当不了 reviewer，happy path
    /// 卡死在第一次 Design review。
    /// 空 = 回退 AgentBoardOptions.StartupToken（旧行为：所有 agent 共用一个
    /// 服务账号，多 agent 协作时评审隔离会失效）。
    /// </summary>
    public string AgentBoardToken { get; set; } = "";
    /// <summary>
    /// Optional URL the readiness probe can HTTP-GET to verify
    /// the agent's external auth is live (e.g. WorkBuddy's MCP
    /// server, Codex's ChatGPT login session). When set, the
    /// probe does a short-timeout HEAD/GET and reports
    /// <c>auth_ready</c> in the snapshot. When null/empty, the
    /// gate is treated as "not configured" (skipped, no failure).
    /// 2026-08-29 round-7 review follow-up: the previous design
    /// only checked <c>ApiKeyEnv</c>, which left WorkBuddy
    /// (ApiKeyEnv="") as a false positive — the CLI was present
    /// but the operator had not yet logged in. The MCP URL is
    /// the most reliable external signal we can probe from the
    /// worker process.
    /// </summary>
    public string? McpUrl { get; set; }
}

public sealed class AgentsOptions
{
    public AgentOptions WorkBuddy { get; set; } = new() { Command = "workbuddy" };
    public AgentOptions MiniMax { get; set; } = new() { Command = "MiniMax" };
    public AgentOptions Codex { get; set; } = new() { Command = "codex" };
    /// <summary>
    /// In-process stand-in adapter. Always returns a synthetic success
    /// decision without spawning any external CLI. Useful for local dev /
    /// smoke when no real CLI is installed; <see cref="Agents.FakeAdapter"/>
    /// never reads this object.
    /// </summary>
    public AgentOptions Fake { get; set; } = new() { Command = "" };
    /// <summary>
    /// Explicitly enabled deterministic HTTP adapter for the cross-stack
    /// Golden Happy Path. Disabled by default and never invokes a model.
    /// </summary>
    public AgentOptions Scenario { get; set; } = new() { Command = "" };
}

// =============================================================================
// Sprint 5: shared process layer
// =============================================================================

public sealed class ProcessExecutorOptions
{
    public int MaxOutputBytes { get; set; } = 100 * 1024;          // 100KB tail
    public int SecretRedactionEnabled { get; set; } = 1;           // 0/1, file-friendly
    public string LogDirectory { get; set; } = "data\\execution-logs";
}

public sealed class AgentBoardOptions
{
    public string HeartbeatUrl { get; set; } = "";
    public string WebSocketUrl { get; set; } = "";
    /// <summary>
    /// PR-12：FastAPI server URL（worker 启动时 register / heartbeat 走这里）。
    /// 默认空字符串 = 不调 FastAPI（向后兼容老部署）。
    /// </summary>
    public string ServerUrl { get; set; } = "";
    /// <summary>
    /// PR-12：可选 bearer token，PR-12 startup service 调 FastAPI 时
    /// 带 <c>Authorization: Bearer {token}</c>。空 = 不带。
    /// </summary>
    public string StartupToken { get; set; } = "";
    /// <summary>
    /// P0-3 (2026-09-01)：fail-fast 开关。true 时启动注册路径视为必需：
    ///   - AgentBoard:ServerUrl 为空 → 直接抛错停机（不再静默跳过）；
    ///   - RabbitMq:Uri 为空 → 直接抛错停机（workflow 消费没有 broker 必然失聪）；
    ///   - upsert 循环结束后没有任何 WorkBuddy/Codex agent 注册成功
    ///     （典型原因：Agents:*.Command 留空）→ 直接抛错停机。
    /// 默认 false = 旧行为（log 后继续，向后兼容本地 dev / 无 server 部署）。
    /// 生产 first-run 应设 true，把配置错误从「scheduler 看不到 agent」
    /// 变成「worker 启动即报错」。
    /// </summary>
    public bool RequireRegistration { get; set; } = false;
}

public sealed class PortalOptions
{
    public string Urls { get; set; } = "http://127.0.0.1:58240";
    public string ApiKey { get; set; } = "";
}
