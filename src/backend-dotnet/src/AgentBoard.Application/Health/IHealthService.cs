// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;

namespace AgentBoard.Application.Health;

/// <summary>
/// Operations on the health-probe surface. The <see cref="GetDatabaseStatusAsync"/>
/// method is the only consumer of <see cref="IDbContext"/>; everything
/// else in the API layer goes through Providers.
/// </summary>
public interface IHealthService : IService
{
    Task<string> GetDatabaseStatusAsync(CancellationToken ct = default);
}
