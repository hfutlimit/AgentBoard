// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Base;
using AgentBoard.Api.Api.Common;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board;
using AgentBoard.Application.Board.Dtos;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Tasks;

/// <summary>Task attachment endpoints (read-only; upload is handled by FastAPI).
/// Mirrors FastAPI <c>/api/tasks/{taskId}/attachments</c>.</summary>
[ApiController]
[Route("api/tasks/{taskId:int}/attachments")]
[Produces("application/json")]
public sealed class TaskAttachmentsController : BaseController<IBoardProvider>
{
    public TaskAttachmentsController(IBoardProvider provider, ICurrentUser current) : base(provider, current) { }

    [HttpGet]
    [ProducesResponseType(typeof(IReadOnlyList<AttachmentDto>), 200)]
    public async Task<ActionResult<IReadOnlyList<AttachmentDto>>> List(
        int taskId, CancellationToken ct) =>
        Ok(await Provider.ListAttachmentsAsync(taskId, ct));

    [HttpGet("{id:int}")]
    [ProducesResponseType(typeof(AttachmentDto), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult<AttachmentDto>> GetInfo(int id, CancellationToken ct)
    {
        var dto = await Provider.GetAttachmentInfoAsync(id, ct);
        return dto is null ? NotFound(new ApiError($"attachment {id} not found")) : Ok(dto);
    }

    [HttpDelete("{id:int}")]
    [ProducesResponseType(204)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<IActionResult> Delete(int id, CancellationToken ct)
    {
        var deleted = await Provider.DeleteAttachmentAsync(id, ct);
        return deleted ? NoContent() : NotFound(new ApiError($"attachment {id} not found"));
    }
}
