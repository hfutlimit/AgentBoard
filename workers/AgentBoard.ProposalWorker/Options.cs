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
}

// =============================================================================
// Sprint 4: Per-agent options. The single worker is configured to know all
// three (workbuddy / MiniMax / codex); each agent has its own CLI command
// and timeout. Setting Command = "" disables an agent at startup.
// =============================================================================

public sealed class AgentOptions
{
    public string Command { get; set; } = "";
    public string WorkingDirectory { get; set; } = "";
    public int TimeoutMinutes { get; set; } = 30;
    public int MaxCapturedOutputChars { get; set; } = 20000;
    public string? ApiKeyEnv { get; set; }  // optional; injected only if set
}

public sealed class AgentsOptions
{
    public AgentOptions WorkBuddy { get; set; } = new() { Command = "workbuddy" };
    public AgentOptions MiniMax { get; set; } = new() { Command = "MiniMax" };
    public AgentOptions Codex { get; set; } = new() { Command = "codex" };
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
}

public sealed class PortalOptions
{
    public string Urls { get; set; } = "http://127.0.0.1:58240";
    public string ApiKey { get; set; } = "";
}
