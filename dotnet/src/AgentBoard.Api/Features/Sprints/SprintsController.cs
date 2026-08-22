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
    public SprintsController(ISprintProvider provider, ICurrentUser current) : base(provider, current) { }

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
        var dto = await Provider.CreateSprintAsync(projectId, body.Title, body.Goal, body.StartDate, body.EndDate, ct);
        return StatusCode(StatusCodes.Status201Created, dto);
    }

    [HttpPatch("{id:int}")]
    [ProducesResponseType(typeof(SprintDto), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    [ProducesResponseType(typeof(ApiError), 422)]
    public async Task<ActionResult<SprintDto>> Patch(
        int id, [FromBody] SprintPatchRequest body, CancellationToken ct)
    {
        var dto = await Provider.UpdateSprintAsync(id, body.Title, body.Goal, body.Status, body.StartDate, body.EndDate, ct);
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
