// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;

namespace AgentBoard.Application.Health;

public sealed class HealthProvider : IHealthProvider
{
    /// <summary>Version string reported by GET /api/health. Bumped per release.</summary>
    public const string ApiVersion = "0.1.0";

    private readonly IHealthService _health;

    public HealthProvider(IHealthService health) =>
        _health = health ?? throw new ArgumentNullException(nameof(health));

    public async Task<HealthProbe> ProbeAsync(CancellationToken ct = default) =>
        new(Database: await _health.GetDatabaseStatusAsync(ct));
}
