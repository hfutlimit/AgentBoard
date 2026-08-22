// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Base;
using AgentBoard.Api.Api.Common;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board;
using AgentBoard.Application.Board.Dtos;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Stories;

/// <summary>Story endpoints. Mirrors FastAPI <c>/api/stories</c>.</summary>
[ApiController]
[Route("api/stories")]
[Produces("application/json")]
public sealed class StoriesController : BaseController<IBoardProvider>
{
    public StoriesController(IBoardProvider provider, ICurrentUser current) : base(provider, current) { }

    [HttpGet]
    [ProducesResponseType(typeof(IReadOnlyList<StoryDto>), 200)]
    public async Task<ActionResult<IReadOnlyList<StoryDto>>> List(
        [FromQuery] int? epicId, CancellationToken ct) =>
        Ok(await Provider.ListStoriesAsync(epicId, ct));

    [HttpGet("{id:int}")]
    [ProducesResponseType(typeof(StoryDto), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult<StoryDto>> Get(int id, CancellationToken ct)
    {
        var dto = await Provider.GetStoryAsync(id, ct);
        return dto is null ? NotFound(new ApiError($"story {id} not found")) : Ok(dto);
    }

    [HttpPost]
    [ProducesResponseType(typeof(StoryDto), 201)]
    [ProducesResponseType(typeof(ApiError), 404)]
    [ProducesResponseType(typeof(ApiError), 422)]
    public async Task<ActionResult<StoryDto>> Create(
        [FromQuery] int epicId,
        [FromBody] StoryCreateRequest body,
        CancellationToken ct)
    {
        var dto = await Provider.CreateStoryAsync(epicId, body.Title, body.Description, body.NeedsDesign, ct);
        return CreatedAtAction(nameof(Get), new { id = dto.Id }, dto);
    }

    [HttpPatch("{id:int}")]
    [ProducesResponseType(typeof(StoryDto), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult<StoryDto>> Update(
        int id,
        [FromBody] StoryPatchRequest body,
        CancellationToken ct)
    {
        var dto = await Provider.UpdateStoryAsync(id, body.Title, body.Description, body.Status, body.NeedsDesign, body.InKanban, ct);
        return dto is null ? NotFound(new ApiError($"story {id} not found")) : Ok(dto);
    }

    [HttpDelete("{id:int}")]
    [ProducesResponseType(204)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<IActionResult> Delete(int id, CancellationToken ct)
    {
        var deleted = await Provider.DeleteStoryAsync(id, ct);
        return deleted ? NoContent() : NotFound(new ApiError($"story {id} not found"));
    }

    [HttpPost("{id:int}/confirm")]
    [ProducesResponseType(typeof(StoryDto), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    [ProducesResponseType(typeof(ApiError), 422)]
    public async Task<ActionResult<StoryDto>> Confirm(int id, CancellationToken ct)
    {
        var dto = await Provider.ConfirmStoryAsync(id, ct);
        return dto is null ? NotFound(new ApiError($"story {id} not found")) : Ok(dto);
    }

    [HttpPost("{id:int}/complete")]
    [ProducesResponseType(typeof(StoryDto), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    [ProducesResponseType(typeof(ApiError), 422)]
    public async Task<ActionResult<StoryDto>> Complete(int id, CancellationToken ct)
    {
        var dto = await Provider.CompleteStoryAsync(id, ct);
        return dto is null ? NotFound(new ApiError($"story {id} not found")) : Ok(dto);
    }

    [HttpGet("{id:int}/status-history")]
    [ProducesResponseType(typeof(IReadOnlyList<StoryStatusHistoryDto>), 200)]
    public async Task<ActionResult<IReadOnlyList<StoryStatusHistoryDto>>> GetStatusHistory(
        int id, CancellationToken ct) =>
        Ok(await Provider.GetStoryStatusHistoryAsync(id, ct));
}
