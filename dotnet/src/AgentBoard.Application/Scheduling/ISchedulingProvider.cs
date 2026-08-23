// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Scheduling.Dtos;

namespace AgentBoard.Application.Scheduling;

/// <summary>
/// Stage 2 module 2: Agents provider. Mirrors the FastAPI
/// <c>/api/agents*</c> router family (see
/// <c>agentboard/features/admin/router.py::agents_*</c>).
///
/// Note on the shared name with the Schedule module (Stage 2 module 4):
/// this provider is intentionally the Agents-only surface. If a future
/// Schedules module also defines <c>ISchedulingProvider</c>, the root
/// session can reconcile via a partial-class extension or a dedicated
/// <c>IAgentScheduleProvider</c> wrapper.
/// </summary>
public interface ISchedulingProvider : IProvider
{
    /// <summary>List all registered agents (enabled and disabled).</summary>
    Task<IReadOnlyList<AgentDto>> ListAgentsAsync(CancellationToken ct = default);

    /// <summary>Register a new agent. Throws <see cref="Domain.Common.DuplicateException"/> if <c>agent_id</c> already exists.</summary>
    Task<AgentDto> RegisterAgentAsync(AgentRegisterRequest request, CancellationToken ct = default);

    /// <summary>Update mutable fields on an existing agent. Returns null when the agent is not found.</summary>
    Task<AgentDto?> UpdateAgentAsync(string agentId, AgentUpdateRequest request, CancellationToken ct = default);

    /// <summary>Hard-delete an agent by <c>agent_id</c>. Returns true when the row existed.</summary>
    Task<bool> DeleteAgentAsync(string agentId, CancellationToken ct = default);

    /// <summary>Probe an agent's liveness. Updates <c>last_probe_at</c> + <c>probe_message</c> and returns the new status.</summary>
    Task<AgentProbeResponse?> ProbeAgentAsync(string agentId, int? timeoutSeconds, CancellationToken ct = default);
}
