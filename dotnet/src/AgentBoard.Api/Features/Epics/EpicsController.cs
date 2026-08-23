// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Base;
using AgentBoard.Api.Api.Common;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board;
using AgentBoard.Application.Board.Dtos;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Epics;

/// <summary>Epic endpoints. Mirrors FastAPI <c>/api/epics</c>.</summary>
[ApiController]
[Route("api/epics")]
[Produces("application/json")]
public sealed class EpicsController : BaseController<IBoardProvider>
{
    public EpicsController(IBoardProvider provider, ICurrentUser current) : base(provider, current) { }

    [HttpGet]
    [ProducesResponseType(typeof(IReadOnlyList<EpicDto>), 200)]
    public async Task<ActionResult<IReadOnlyList<EpicDto>>> List(
        [FromQuery] int? projectId, CancellationToken ct) =>
        Ok(await Provider.ListEpicsAsync(projectId, ct));

    [HttpGet("{id:int}")]
    [ProducesResponseType(typeof(EpicDto), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult<EpicDto>> Get(int id, CancellationToken ct)
    {
        var dto = await Provider.GetEpicAsync(id, ct);
        return dto is null ? NotFound(new ApiError($"epic {id} not found")) : Ok(dto);
    }

    [HttpPost]
    [ProducesResponseType(typeof(EpicDto), 201)]
    [ProducesResponseType(typeof(ApiError), 404)]
    [ProducesResponseType(typeof(ApiError), 422)]
    public async Task<ActionResult<EpicDto>> Create(
        [FromQuery] int projectId,
        [FromBody] EpicCreateRequest body,
        CancellationToken ct)
    {
        var dto = await Provider.CreateEpicAsync(projectId, body.Title, body.Description, ct);
        return CreatedAtAction(nameof(Get), new { id = dto.Id }, dto);
    }

    [HttpPatch("{id:int}")]
    [ProducesResponseType(typeof(EpicDto), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult<EpicDto>> Update(
        int id,
        [FromBody] EpicPatchRequest body,
        CancellationToken ct)
    {
        var dto = await Provider.UpdateEpicAsync(id, body.Title, body.Description, body.Status, ct);
        return dto is null ? NotFound(new ApiError($"epic {id} not found")) : Ok(dto);
    }

    [HttpDelete("{id:int}")]
    [ProducesResponseType(204)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<IActionResult> Delete(int id, CancellationToken ct)
    {
        var deleted = await Provider.DeleteEpicAsync(id, ct);
        return deleted ? NoContent() : NotFound(new ApiError($"epic {id} not found"));
    }

    // ===================== P6: nested Story create (BFF module 6, 2026-08-23) =====================

    /// <summary>
    /// Create a Story under an Epic. Mirrors FastAPI <c>POST /api/epics/{eid}/stories</c>.
    /// </summary>
    [HttpPost("{epicId:int}/stories")]
    [ProducesResponseType(typeof(StoryDto), 201)]
    [ProducesResponseType(typeof(ApiError), 404)]
    [ProducesResponseType(typeof(ApiError), 422)]
    public async Task<ActionResult<StoryDto>> CreateStory(
        int epicId,
        [FromBody] StoryCreateRequest body,
        CancellationToken ct)
    {
        var dto = await Provider.CreateEpicStoryAsync(epicId, body.Title, body.Description, ct);
        return StatusCode(StatusCodes.Status201Created, dto);
    }
}
