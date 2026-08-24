// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Base;
using AgentBoard.Api.Api.Common;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board;
using AgentBoard.Application.Board.Dtos;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Reviews;

/// <summary>Review statistics and reassign-timeout. Mirrors FastAPI review-stats router.</summary>
[ApiController]
[Route("api/review-stats")]
[Produces("application/json")]
public sealed class ReviewStatsController : BaseController<IBoardProvider>
{
    public ReviewStatsController(IBoardProvider provider, ICurrentUser current) : base(provider, current) { }

    [HttpGet]
    [ProducesResponseType(typeof(ReviewStatsDto), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult<ReviewStatsDto>> Get(
        [FromQuery(Name = "project_id")] int projectId,
        [FromQuery] int days = 30,
        [FromQuery(Name = "user_id")] int? userId = null,
        CancellationToken ct = default)
    {
        var dto = await Provider.GetReviewStatsAsync(projectId, days, userId, ct);
        return dto is null ? NotFound(new ApiError($"project {projectId} not found")) : Ok(dto);
    }

    [HttpPost("reassign-timeout")]
    [ProducesResponseType(typeof(object), 200)]
    public async Task<ActionResult> ReassignTimeout(
        [FromBody] ReassignTimeoutRequest? body = null,
        CancellationToken ct = default)
    {
        // Stub: returns ok. Full implementation requires background job infrastructure.
        return Ok(new { ok = true, message = "reassign-timeout stub" });
    }
}
