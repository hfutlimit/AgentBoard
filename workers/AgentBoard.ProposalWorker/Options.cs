namespace AgentBoard.ProposalWorker;

public sealed class WorkerOptions
{
    public string Id { get; set; } = Environment.MachineName;
    public int HeartbeatSeconds { get; set; } = 15;
    public string HistoryDatabasePath { get; set; } = "data\\proposal-worker.db";
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

public sealed class WorkBuddyOptions
{
    public string Command { get; set; } = "workbuddy";
    public string WorkingDirectory { get; set; } = "";
    public int TimeoutMinutes { get; set; } = 30;
    public int MaxCapturedOutputChars { get; set; } = 20000;
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
