// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Base;
using AgentBoard.Api.Api.Common;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Comments;

/// <summary>Read-only comment endpoints. Mirrors FastAPI <c>/api/comments</c>.</summary>
[ApiController]
[Route("api/comments")]
[Produces("application/json")]
public sealed class CommentsController : BaseController<IBoardProvider>
{
    public CommentsController(IBoardProvider provider, ICurrentUser current) : base(provider, current) { }

    [HttpGet]
    [ProducesResponseType(typeof(IReadOnlyList<AgentBoard.Application.Board.Dtos.CommentDto>), 200)]
    public async Task<ActionResult<IReadOnlyList<AgentBoard.Application.Board.Dtos.CommentDto>>> List(
        [FromQuery] int? taskId, [FromQuery] int? storyId, [FromQuery] int? epicId, CancellationToken ct) =>
        Ok(await Provider.ListCommentsAsync(taskId, storyId, epicId, ct));

    [HttpGet("{id:int}")]
    [ProducesResponseType(typeof(AgentBoard.Application.Board.Dtos.CommentDto), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult<AgentBoard.Application.Board.Dtos.CommentDto>> Get(int id, CancellationToken ct)
    {
        var dto = await Provider.GetCommentAsync(id, ct);
        return dto is null ? NotFound(new ApiError($"comment {id} not found")) : Ok(dto);
    }
}
