// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Entities;

namespace AgentBoard.Application.Abstractions;

/// <summary>
/// Repository contract for the <c>agents</c> table.
///
/// Adds an <c>agent_id</c> (string) lookup on top of the generic
/// <see cref="IRepository{T}"/> CRUD surface — the Stage 2 controller
/// routes <c>PUT/DELETE /api/agents/{agentId}</c> by the external string
/// id, not the integer PK.
/// </summary>
public interface IAgentRepository : IRepository<Agent>
{
    /// <summary>Look up an agent by its external <c>agent_id</c> string. Returns null when not found.</summary>
    Task<Agent?> GetByAgentIdAsync(string agentId, CancellationToken ct = default);
}
