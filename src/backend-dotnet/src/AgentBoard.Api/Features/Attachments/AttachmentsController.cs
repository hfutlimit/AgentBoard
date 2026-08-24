// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Base;
using AgentBoard.Api.Api.Common;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board;
using AgentBoard.Application.Board.Dtos;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Attachments;

/// <summary>Attachment endpoints (id-keyed, not nested under task).
/// Mirrors FastAPI <c>/api/attachments/{id}/info</c>.</summary>
[ApiController]
[Route("api/attachments")]
[Produces("application/json")]
public sealed class AttachmentsController : BaseController<IBoardProvider>
{
    public AttachmentsController(IBoardProvider provider, ICurrentUser current) : base(provider, current) { }

    [HttpGet("{id:int}/info")]
    [ProducesResponseType(typeof(AttachmentDto), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult<AttachmentDto>> GetInfo(int id, CancellationToken ct)
    {
        var dto = await Provider.GetAttachmentInfoAsync(id, ct);
        return dto is null ? NotFound(new ApiError($"attachment {id} not found")) : Ok(dto);
    }
}
