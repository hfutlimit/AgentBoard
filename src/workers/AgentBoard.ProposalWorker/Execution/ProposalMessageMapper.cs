using AgentBoard.ProposalWorker.Agents;
using Microsoft.Extensions.Options;

namespace AgentBoard.ProposalWorker.Execution;

/// <summary>
/// Sprint 4. The single translation point between the Proposal-shaped
/// RabbitMQ message and the workload-agnostic <see cref="ExecutionRequest"/>.
/// Worker code outside this class must not know what a Proposal is.
/// </summary>
public sealed class ProposalMessageMapper
{
    private readonly IAgentAdapterRegistry _registry;

    public ProposalMessageMapper(IAgentAdapterRegistry registry) => _registry = registry;

    public ExecutionRequest MapToExecution(ProposalMessage msg, string source, string defaultAgent = "workbuddy")
    {
        // Backward compat: old messages without agent_type default to workbuddy.
        var agentType = string.IsNullOrWhiteSpace(msg.AgentType) ? defaultAgent : msg.AgentType;
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
