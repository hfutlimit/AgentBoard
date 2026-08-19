// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;

namespace AgentBoard.Application.Health;

/// <summary>
/// Health-probe Provider. Composes the <see cref="IHealthService"/>
/// (database check) with the .NET API version string. The
/// <c>HealthProvider.ApiVersion</c> constant is the single source of truth
/// the .NET BFF reports back; the FastAPI service uses
/// <c>"0.4"</c> today, we use <c>"0.1.0"</c> while the BFF is in
/// stage 0 — the two will be re-aligned at stage 3.
/// </summary>
public interface IHealthProvider : IProvider
{
    Task<HealthProbe> ProbeAsync(CancellationToken ct = default);
}

/// <summary>Internal health-probe DTO. Mapped to the public
/// <c>HealthResponseDto</c> in the API layer so the wire format is
/// owned by Api/Common (kept free of EF Core types).</summary>
public sealed record HealthProbe(string Database);
