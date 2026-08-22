// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Base;
using AgentBoard.Api.Api.Common;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board;
using AgentBoard.Application.Board.Dtos;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Stories;

/// <summary>Read-only story endpoints. Mirrors FastAPI <c>/api/stories</c>.</summary>
[ApiController]
[Route("api/stories")]
[Produces("application/json")]
public sealed class StoriesController : BaseController<IBoardProvider>
{
    public StoriesController(IBoardProvider provider, ICurrentUser current) : base(provider, current) { }

    [HttpGet]
    [ProducesResponseType(typeof(IReadOnlyList<AgentBoard.Application.Board.Dtos.StoryDto>), 200)]
    public async Task<ActionResult<IReadOnlyList<AgentBoard.Application.Board.Dtos.StoryDto>>> List(
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
}
