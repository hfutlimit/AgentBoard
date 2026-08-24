// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Domain.Entities;
using Microsoft.EntityFrameworkCore;

namespace AgentBoard.Infrastructure.Persistence.Repositories;

/// <summary>
/// EF Core implementation of <see cref="IAgentRepository"/>. The interface
/// lives in the Application layer (Clean Architecture); this file is the
/// only place that knows about <c>AppDbContext</c>.
///
/// Uses <c>Db.Set&lt;Agent&gt;()</c> rather than a typed DbSet property so
/// the read model works without modifying <c>AppDbContext</c> (Stage 2
/// follow-up: AppDbContext is owned by the root session; the new
/// <c>agents</c> table is registered through
/// <see cref="Configurations.AgentConfiguration"/> which
/// <c>ApplyConfigurationsFromAssembly</c> auto-discovers).
/// </summary>
public sealed class AgentRepository : Repository<Agent>, IAgentRepository
{
    public AgentRepository(AppDbContext db) : base(db) { }
    protected override DbSet<Agent> Set => Db.Set<Agent>();

    public Task<Agent?> GetByAgentIdAsync(string agentId, CancellationToken ct = default)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(agentId);
        return Set.AsNoTracking().FirstOrDefaultAsync(a => a.AgentId == agentId, ct);
    }
}
