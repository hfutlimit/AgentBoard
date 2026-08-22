// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Base;
using AgentBoard.Api.Api.Common;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board;
using AgentBoard.Application.Board.Dtos;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Comments;

/// <summary>
/// Comment endpoints. Route layout mirrors FastAPI's <c>work_items</c> router 1:1:
/// <c>GET/POST /api/tasks/{tid}/comments</c>,
/// <c>GET/POST /api/stories/{sid}/comments</c>,
/// <c>GET/POST /api/epics/{eid}/comments</c>,
/// <c>GET /api/comments/{id}</c> (detail), <c>DELETE /api/comments/{id}</c>.
/// </summary>
[ApiController]
[Route("api")]
[Produces("application/json")]
public sealed class CommentsController : BaseController<IBoardProvider>
{
    public CommentsController(IBoardProvider provider, ICurrentUser current) : base(provider, current) { }

    // ---------- Task comments ----------

    [HttpGet("tasks/{tid:int}/comments")]
    [ProducesResponseType(typeof(IReadOnlyList<CommentDto>), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult<IReadOnlyList<CommentDto>>> ListTaskComments(int tid, CancellationToken ct) =>
        Ok(await Provider.ListCommentsAsync(tid, null, null, ct));

    [HttpPost("tasks/{tid:int}/comments")]
    [ProducesResponseType(typeof(CommentDto), 201)]
    [ProducesResponseType(typeof(ApiError), 404)]
    [ProducesResponseType(typeof(ApiError), 422)]
    public async Task<ActionResult<CommentDto>> CreateTaskComment(
        int tid, [FromBody] CommentCreateRequest body, CancellationToken ct)
    {
        var dto = await Provider.CreateCommentAsync(tid, null, null, body.Author, body.Content, ct);
        return StatusCode(StatusCodes.Status201Created, dto);
    }

    // ---------- Story comments ----------

    [HttpGet("stories/{sid:int}/comments")]
    [ProducesResponseType(typeof(IReadOnlyList<CommentDto>), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult<IReadOnlyList<CommentDto>>> ListStoryComments(int sid, CancellationToken ct) =>
        Ok(await Provider.ListCommentsAsync(null, sid, null, ct));

    [HttpPost("stories/{sid:int}/comments")]
    [ProducesResponseType(typeof(CommentDto), 201)]
    [ProducesResponseType(typeof(ApiError), 404)]
    [ProducesResponseType(typeof(ApiError), 422)]
    public async Task<ActionResult<CommentDto>> CreateStoryComment(
        int sid, [FromBody] CommentCreateRequest body, CancellationToken ct)
    {
        var dto = await Provider.CreateCommentAsync(null, sid, null, body.Author, body.Content, ct);
        return StatusCode(StatusCodes.Status201Created, dto);
    }

    // ---------- Epic comments ----------

    [HttpGet("epics/{eid:int}/comments")]
    [ProducesResponseType(typeof(IReadOnlyList<CommentDto>), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult<IReadOnlyList<CommentDto>>> ListEpicComments(int eid, CancellationToken ct) =>
        Ok(await Provider.ListCommentsAsync(null, null, eid, ct));

    [HttpPost("epics/{eid:int}/comments")]
    [ProducesResponseType(typeof(CommentDto), 201)]
    [ProducesResponseType(typeof(ApiError), 404)]
    [ProducesResponseType(typeof(ApiError), 422)]
    public async Task<ActionResult<CommentDto>> CreateEpicComment(
        int eid, [FromBody] CommentCreateRequest body, CancellationToken ct)
    {
        var dto = await Provider.CreateCommentAsync(null, null, eid, body.Author, body.Content, ct);
        return StatusCode(StatusCodes.Status201Created, dto);
    }

    // ---------- Detail + delete ----------

    [HttpGet("comments/{id:int}")]
    [ProducesResponseType(typeof(CommentDto), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult<CommentDto>> Get(int id, CancellationToken ct)
    {
        var dto = await Provider.GetCommentAsync(id, ct);
        return dto is null ? NotFound(new ApiError($"comment {id} not found")) : Ok(dto);
    }

    [HttpDelete("comments/{id:int}")]
    [ProducesResponseType(typeof(OkResult), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult> Delete(int id, CancellationToken ct)
    {
        var ok = await Provider.DeleteCommentAsync(id, ct);
        return ok ? Ok(new { ok = true }) : NotFound(new ApiError("comment not found"));
    }
}
