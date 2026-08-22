// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Base;
using AgentBoard.Api.Api.Common;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board;
using AgentBoard.Application.Board.Dtos;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Documents;

/// <summary>
/// Document comment update/delete. Mirrors FastAPI <c>document-comments</c> router.
/// Create + list live on the DocumentsController under <c>/api/documents/{id}/comments</c>.
/// </summary>
[ApiController]
[Route("api")]
[Produces("application/json")]
public sealed class DocumentCommentsController : BaseController<IDocumentProvider>
{
    public DocumentCommentsController(IDocumentProvider provider, ICurrentUser current)
        : base(provider, current) { }

    /// <summary>PATCH /api/document-comments/{commentId}</summary>
    [HttpPatch("document-comments/{commentId:int}")]
    [ProducesResponseType(typeof(DocumentCommentDto), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    [ProducesResponseType(typeof(ApiError), 422)]
    public async Task<ActionResult<DocumentCommentDto>> Update(
        int commentId, [FromBody] DocumentCommentUpdateRequest body, CancellationToken ct)
    {
        var author = CurrentUser.Username ?? string.Empty;
        var dto = await Provider.UpdateDocumentCommentAsync(commentId, body.Content, author, ct);
        return dto is null ? NotFound(new ApiError($"comment {commentId} not found")) : Ok(dto);
    }

    /// <summary>DELETE /api/document-comments/{commentId}</summary>
    [HttpDelete("document-comments/{commentId:int}")]
    [ProducesResponseType(typeof(object), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult> Delete(int commentId, CancellationToken ct)
    {
        var ok = await Provider.DeleteDocumentCommentAsync(commentId, ct);
        return ok ? Ok(new { ok = true }) : NotFound(new ApiError($"comment {commentId} not found"));
    }
}
