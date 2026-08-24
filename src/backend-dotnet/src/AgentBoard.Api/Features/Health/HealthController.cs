// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Base;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Health;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Health;

/// <summary>
/// Liveness / readiness probe. Returns 200 with a <see cref="HealthResponseDto"/>
/// payload that mirrors FastAPI's <c>GET /api/health</c>:
/// <c>{status, database, version, timestamp}</c>.
///
/// The DB probe delegates to <see cref="IHealthProvider"/> so the Controller
/// stays free of any EF Core or IDbContext reference (enforced by the
/// NetArchTest layered-architecture rules).
/// </summary>
[ApiController]
[Route("api/health")]
[Produces("application/json")]
public sealed class HealthController : BaseController<IHealthProvider>
{
    private readonly IClock _clock;

    public HealthController(IHealthProvider provider, ICurrentUser current, IClock clock)
        : base(provider, current) =>
        _clock = clock ?? throw new ArgumentNullException(nameof(clock));

    [HttpGet]
    [ProducesResponseType(typeof(HealthResponseDto), StatusCodes.Status200OK)]
    public async Task<ActionResult<HealthResponseDto>> Get(CancellationToken ct)
    {
        var probe = await Provider.ProbeAsync(ct);
        return Ok(new HealthResponseDto(
            Status:    "ok",
            Database:  probe.Database,
            Version:   HealthProvider.ApiVersion,
            Timestamp: _clock.UtcNow));
    }
}
