// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Common;
using AgentBoard.Application.Board;
using AgentBoard.Application.Board.Dtos;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Documents;

/// <summary>Document folder endpoints. Mirrors FastAPI <c>/api/document-folders</c>.</summary>
[ApiController]
[Route("api/document-folders")]
[Produces("application/json")]
public sealed class DocumentFoldersController : ControllerBase
{
    private readonly IDocumentProvider _provider;

    public DocumentFoldersController(IDocumentProvider provider)
    {
        _provider = provider;
    }

    [HttpGet]
    [ProducesResponseType(typeof(IReadOnlyList<DocumentFolderDto>), 200)]
    public async Task<ActionResult> List(
        [FromQuery(Name = "project_id")] int? projectId,
        [FromQuery(Name = "parent_id")] int? parentId,
        CancellationToken ct)
    {
        if (projectId is null) return BadRequest(new ApiError("project_id is required"));
        var (items, total) = await _provider.ListFoldersAsync(projectId, parentId, ct);
        return Ok(new { items, total });
    }

    [HttpPost]
    [ProducesResponseType(typeof(DocumentFolderDto), 201)]
    [ProducesResponseType(typeof(ApiError), 400)]
    public async Task<ActionResult<DocumentFolderDto>> Create(
        [FromBody] DocumentFolderCreateRequest body, CancellationToken ct)
    {
        if (body.ProjectId is null) return BadRequest(new ApiError("project_id is required"));
        var dto = await _provider.CreateFolderAsync(body.ProjectId.Value, body.ParentId, body.Name, ct);
        return StatusCode(201, dto);
    }

    [HttpDelete("{id:int}")]
    [ProducesResponseType(204)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<IActionResult> Delete(int id, CancellationToken ct)
    {
        var ok = await _provider.DeleteFolderAsync(id, ct);
        return ok ? NoContent() : NotFound(new ApiError($"folder {id} not found"));
    }
}
