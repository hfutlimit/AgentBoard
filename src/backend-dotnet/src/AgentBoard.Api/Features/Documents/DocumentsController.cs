// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Common;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board;
using AgentBoard.Application.Board.Dtos;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Documents;

[ApiController]
[Route("api/documents")]
[Produces("application/json")]
public sealed class DocumentsController : ControllerBase
{
    private readonly IDocumentProvider _provider;
    private readonly ICurrentUser _current;

    public DocumentsController(IDocumentProvider provider, ICurrentUser current)
    {
        _provider = provider; _current = current;
    }

    [HttpGet]
    public async Task<ActionResult> List(
        [FromQuery(Name = "project_id")] int? projectId,
        [FromQuery] string? type, [FromQuery] string? status, [FromQuery] string? q,
        [FromQuery(Name = "folder_id")] int? folderId,
        [FromQuery] int limit = 100, [FromQuery] int offset = 0, CancellationToken ct = default)
    {
        var (items, total) = await _provider.ListDocumentsAsync(projectId, type, status, q, folderId, limit, offset, ct);
        return Ok(new { items, total });
    }

    [HttpGet("{id:int}")]
    public async Task<ActionResult<DocumentDto>> Get(int id, CancellationToken ct)
    {
        var dto = await _provider.GetDocumentAsync(id, ct);
        return dto is null ? NotFound(new ApiError($"document {id} not found")) : Ok(dto);
    }

    [HttpPost]
    public async Task<ActionResult<DocumentDto>> Create([FromBody] DocumentCreateRequest body, CancellationToken ct)
    {
        if (body.ProjectId is null) return BadRequest(new ApiError("project_id is required"));
        var dto = await _provider.CreateDocumentAsync(
            body.ProjectId.Value, body.Title, body.Content, body.Type,
            body.AuthorId ?? _current.UserId, body.EpicId, body.StoryId, body.FolderId, ct);
        return StatusCode(201, dto);
    }

    [HttpPatch("{id:int}")]
    public async Task<ActionResult<DocumentDto>> Patch(int id, [FromBody] DocumentPatchRequest body, CancellationToken ct)
    {
        var dto = await _provider.UpdateDocumentAsync(id, body.Title, body.Content, body.Type, body.FolderId, body.EpicId, body.StoryId, ct);
        return dto is null ? NotFound(new ApiError($"document {id} not found")) : Ok(dto);
    }

    [HttpDelete("{id:int}")]
    public async Task<ActionResult> Delete(int id, CancellationToken ct)
    {
        var ok = await _provider.DeleteDocumentAsync(id, ct);
        return ok ? Ok(new { ok = true }) : NotFound(new ApiError($"document {id} not found"));
    }

    [HttpPut("{id:int}/status")]
    public async Task<ActionResult<DocumentDto>> SetStatus(int id, [FromBody] DocumentStatusRequest body, CancellationToken ct)
    {
        var dto = await _provider.SetDocumentStatusAsync(id, body.Status, ct);
        return dto is null ? NotFound(new ApiError($"document {id} not found")) : Ok(dto);
    }

    // ---- Comments ----

    [HttpGet("{id:int}/comments/count")]
    public async Task<ActionResult> CountComments(int id, CancellationToken ct)
    {
        var count = await _provider.CountDocumentCommentsAsync(id, ct);
        return Ok(new { count });
    }

    [HttpGet("{id:int}/comments")]
    public async Task<ActionResult> ListComments(int id, CancellationToken ct)
    {
        var (items, total) = await _provider.ListDocumentCommentsAsync(id, ct);
        return Ok(new { items, total });
    }

    [HttpPost("{id:int}/comments")]
    public async Task<ActionResult<DocumentCommentDto>> CreateComment(int id, [FromBody] DocumentCommentCreateRequest body, CancellationToken ct)
    {
        var dto = await _provider.CreateDocumentCommentAsync(id, body.Author, body.Content, ct);
        return StatusCode(201, dto);
    }

    // ---- Revisions ----

    [HttpGet("{documentId:int}/revisions")]
    public async Task<ActionResult> ListRevisions(int documentId, [FromQuery] int limit = 50, [FromQuery] int offset = 0, CancellationToken ct = default)
    {
        var (items, total) = await _provider.ListRevisionsAsync(documentId, limit, offset, ct);
        return Ok(new { items, total });
    }

    [HttpGet("{documentId:int}/revisions/{revisionNumber:int}")]
    public async Task<ActionResult<DocumentRevisionDto>> GetRevision(int documentId, int revisionNumber, CancellationToken ct)
    {
        var dto = await _provider.GetRevisionAsync(documentId, revisionNumber, ct);
        return dto is null ? NotFound(new ApiError($"revision {revisionNumber} not found")) : Ok(dto);
    }

    [HttpPost("{documentId:int}/revisions")]
    public async Task<ActionResult<DocumentRevisionDto>> SaveRevision(int documentId, [FromBody] RevisionSaveRequest body, CancellationToken ct)
    {
        var dto = await _provider.SaveRevisionAsync(documentId, body.Content, body.ChangeNote, body.Author, ct);
        return StatusCode(201, dto);
    }

    [HttpPost("{documentId:int}/revisions/restore")]
    public async Task<ActionResult<DocumentRevisionDto>> RestoreRevision(int documentId, [FromBody] RevisionRestoreRequest body, CancellationToken ct)
    {
        var dto = await _provider.RestoreRevisionAsync(documentId, 0, body.ChangeNote, body.Author, ct);
        // Note: revision_number should come from query or body; simplified for now
        return Ok(dto);
    }
}
