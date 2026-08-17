namespace AgentBoard.ProposalWorker.Agents;

/// <summary>
/// Sprint 4. One implementation per agent CLI. The single worker holds
/// three of these (workbuddy / MiniMax / codex) and routes by AgentType.
/// </summary>
public interface IAgentAdapter
{
    string AgentType { get; }
    Task<AgentExecutionResult> ExecuteAsync(ExecutionContext context, CancellationToken ct);
}

public sealed class InvalidAgentException(string agentType)
    : Exception($"agent_type '{agentType}' is not registered on this worker")
{
    public string AgentType { get; } = agentType;
}

/// <summary>
/// Singleton DI registry. Adapters are registered at startup; the worker
/// fails fast if a message arrives for an agent it doesn't know.
/// </summary>
public interface IAgentAdapterRegistry
{
    IAgentAdapter Get(string agentType);
    IReadOnlyCollection<string> RegisteredAgents { get; }
    bool IsRegistered(string agentType);
}

public sealed class AgentAdapterRegistry : IAgentAdapterRegistry
{
    private readonly Dictionary<string, IAgentAdapter> _byType;
    private readonly ILogger<AgentAdapterRegistry> _log;

    public AgentAdapterRegistry(IEnumerable<IAgentAdapter> adapters, ILogger<AgentAdapterRegistry> log)
    {
        _byType = adapters.ToDictionary(a => a.AgentType, StringComparer.OrdinalIgnoreCase);
        _log = log;
        _log.LogInformation("Registered agents: [{List}]", string.Join(", ", _byType.Keys));
    }

    public IReadOnlyCollection<string> RegisteredAgents => _byType.Keys.ToArray();
    public bool IsRegistered(string agentType) => _byType.ContainsKey(agentType);

    public IAgentAdapter Get(string agentType)
    {
        if (_byType.TryGetValue(agentType, out var adapter)) return adapter;
        throw new InvalidAgentException(agentType);
    }
}
