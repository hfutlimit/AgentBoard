// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Base;
using AgentBoard.Api.Api.Common;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board;
using AgentBoard.Application.Board.Dtos;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Sprints;

/// <summary>Sprint CRUD + lifecycle. Mirrors FastAPI <c>/api/sprints</c>.</summary>
[ApiController]
[Route("api/sprints")]
[Produces("application/json")]
public sealed class SprintsController : BaseController<ISprintProvider>
{
    private readonly IBoardProvider _board;

    public SprintsController(ISprintProvider provider, IBoardProvider board, ICurrentUser current) : base(provider, current) =>
        _board = board ?? throw new ArgumentNullException(nameof(board));

    [HttpGet]
    [ProducesResponseType(typeof(IReadOnlyList<SprintDto>), 200)]
    public async Task<ActionResult<IReadOnlyList<SprintDto>>> List(
        [FromQuery(Name = "project_id")] int projectId, CancellationToken ct) =>
        Ok(await Provider.ListSprintsAsync(projectId, ct));

    [HttpGet("{id:int}")]
    [ProducesResponseType(typeof(SprintDto), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult<SprintDto>> Get(int id, CancellationToken ct)
    {
        var dto = await Provider.GetSprintAsync(id, ct);
        return dto is null ? NotFound(new ApiError($"sprint {id} not found")) : Ok(dto);
    }

    [HttpPost]
    [ProducesResponseType(typeof(SprintDto), 201)]
    [ProducesResponseType(typeof(ApiError), 422)]
    public async Task<ActionResult<SprintDto>> Create(
        [FromQuery(Name = "project_id")] int projectId,
        [FromBody] SprintCreateRequest body, CancellationToken ct)
    {
        var dto = await Provider.CreateSprintAsync(
            projectId, new CreateSprintRequest(body.Title, body.Goal, body.StartDate, body.EndDate), ct);
        return StatusCode(StatusCodes.Status201Created, dto);
    }

    [HttpPatch("{id:int}")]
    [ProducesResponseType(typeof(SprintDto), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    [ProducesResponseType(typeof(ApiError), 422)]
    public async Task<ActionResult<SprintDto>> Patch(
        int id, [FromBody] SprintPatchRequest body, CancellationToken ct)
    {
        var dto = await Provider.UpdateSprintAsync(
            id, new UpdateSprintRequest(body.Title, body.Goal, body.Status, body.StartDate, body.EndDate), ct);
        return dto is null ? NotFound(new ApiError($"sprint {id} not found")) : Ok(dto);
    }

    [HttpDelete("{id:int}")]
    [ProducesResponseType(typeof(object), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult> Delete(int id, CancellationToken ct)
    {
        var ok = await Provider.DeleteSprintAsync(id, ct);
        return ok ? Ok(new { ok = true }) : NotFound(new ApiError($"sprint {id} not found"));
    }

    [HttpGet("{id:int}/burndown")]
    [ProducesResponseType(typeof(SprintBurndownDto), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult<SprintBurndownDto>> Burndown(
        int id, [FromQuery] int days = 14, CancellationToken ct = default)
    {
        var dto = await _board.GetSprintBurndownAsync(id, days, ct);
        return dto is null ? NotFound(new ApiError($"sprint {id} not found")) : Ok(dto);
    }

    [HttpGet("{id:int}/tasks")]
    [ProducesResponseType(200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult> Tasks(
        int id,
        [FromQuery] string? status,
        [FromQuery] int limit = 100,
        [FromQuery] int offset = 0,
        CancellationToken ct = default)
    {
        if (await Provider.GetSprintAsync(id, ct) is null)
            return NotFound(new ApiError($"sprint {id} not found"));

        limit = Math.Clamp(limit, 1, 200);
        offset = Math.Max(offset, 0);
        var result = await _board.ListSprintTasksAsync(id, status, limit, offset, ct);
        return Ok(new
        {
            items = result.Items,
            page = offset / limit + 1,
            page_size = limit,
            total = result.Total,
        });
    }

    [HttpPost("{id:int}/activate")]
    [ProducesResponseType(typeof(SprintDto), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult<SprintDto>> Activate(int id, CancellationToken ct)
    {
        var dto = await Provider.ActivateSprintAsync(id, ct);
        return dto is null ? NotFound(new ApiError($"sprint {id} not found")) : Ok(dto);
    }

    [HttpPost("{id:int}/complete")]
    [ProducesResponseType(typeof(SprintDto), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult<SprintDto>> Complete(int id, CancellationToken ct)
    {
        var dto = await Provider.CompleteSprintAsync(id, ct);
        return dto is null ? NotFound(new ApiError($"sprint {id} not found")) : Ok(dto);
    }
}
