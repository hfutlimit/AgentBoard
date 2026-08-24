// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;

namespace AgentBoard.Application.Health;

/// <summary>
/// Default health-check implementation. Wraps <see cref="IDbContext.CanConnectAsync"/>
/// in a try/catch so a single failing probe never propagates as an
/// unhandled exception. The Service returns one of two status strings;
/// the Provider maps that into a structured <c>HealthResponseDto</c>.
/// </summary>
public sealed class HealthService : IHealthService
{
    private readonly IDbContext _db;

    public HealthService(IDbContext db) => _db = db ?? throw new ArgumentNullException(nameof(db));

    public async Task<string> GetDatabaseStatusAsync(CancellationToken ct = default)
    {
        try
        {
            return await _db.CanConnectAsync(ct) ? "ok" : "error";
        }
        catch
        {
            // The health endpoint must never throw — return the explicit
            // "error" status so dashboards can flag the dependency.
            return "error";
        }
    }
}
