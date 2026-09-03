using AgentBoard.Node.Agents;
using Microsoft.Extensions.Logging.Abstractions;

namespace AgentBoard.Node.Execution;

/// <summary>
/// Sprint 4. The single translation point between the Proposal-shaped
/// RabbitMQ message and the workload-agnostic <see cref="ExecutionRequest"/>.
/// Worker code outside this class must not know what a Proposal is.
/// </summary>
public sealed class ProposalMessageMapper
{
    private readonly IAgentAdapterRegistry _registry;
    private readonly ILogger<ProposalMessageMapper> _log;
    private readonly string? _defaultAgent;

    // log/defaultAgent are optional so tests can construct the mapper with a
    // registry only (the pre-e1e8ed7 single-argument shape).
    public ProposalMessageMapper(IAgentAdapterRegistry registry,
                                ILogger<ProposalMessageMapper>? log = null,
                                string? defaultAgent = null)
    {
        _registry = registry;
        _log = log ?? NullLogger<ProposalMessageMapper>.Instance;
        _defaultAgent = string.IsNullOrWhiteSpace(defaultAgent) ? null : defaultAgent;
    }

    public ExecutionRequest MapToExecution(ProposalMessage msg, string source)
    {
        // 2026-09-02: proposal analysis default is the operator-configured
        // Agents:DefaultAgent (injected from Program.cs, same source as
        // WorkflowMessageMapper). The earlier hard-coded "Glm53F" default
        // referenced an agent slot the C# AgentsOptions class never modeled,
        // so every server message without agent_type hit InvalidAgentException
        // and was DLQ'd. Fallback chain: msg.AgentType (server-set, preferred)
        // → Agents:DefaultAgent → "workbuddy" (always registered).
        var fallback = string.IsNullOrWhiteSpace(_defaultAgent) ? "workbuddy" : _defaultAgent;
        var agentType = string.IsNullOrWhiteSpace(msg.AgentType) ? fallback : msg.AgentType;
        // 2026-09-02 (operator verify): emit one log line per proposal
        // dispatch so a reviewer can confirm which agent (and the
        // route) the worker actually picked. Includes the proposal id,
        // the agent_type that won, and whether it came from the
        // server-set msg.AgentType (preferred) or the local default.
        _log.LogInformation(
            "ProposalMessageMapper: proposal={Pid} round={Round} source={Src} " +
            "msg.AgentType={MsgAT} defaultAgent={Def} picked={Picked}",
            msg.ProposalId, msg.Round, source,
            string.IsNullOrWhiteSpace(msg.AgentType) ? "(null)" : msg.AgentType,
            fallback, agentType);
        // Fail fast on unknown agent so the dispatcher doesn't burn a slot.
        if (!_registry.IsRegistered(agentType))
            throw new InvalidAgentException(agentType);
        return new ExecutionRequest(
            ExecutionKey: $"proposal:{msg.ProposalId}:{msg.Round}:{agentType}",
            WorkloadType: "proposal",
            WorkloadId: msg.ProposalId,
            AgentType: agentType,
            Round: msg.Round,
            Source: source,
            PayloadJson: msg.ToJson());
    }
}
