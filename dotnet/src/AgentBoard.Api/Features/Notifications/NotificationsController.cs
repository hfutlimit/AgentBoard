// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Base;
using AgentBoard.Api.Api.Common;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Notifications;

/// <summary>Notification reads. Mirrors FastAPI <c>/api/notifications</c> (reads only; writes land in P2).</summary>
[ApiController]
[Route("api/notifications")]
[Produces("application/json")]
public sealed class NotificationsController : BaseController<IBoardProvider>
{
    public NotificationsController(IBoardProvider provider, ICurrentUser current) : base(provider, current) { }

    [HttpGet]
    [ProducesResponseType(typeof(AgentBoard.Application.Board.Dtos.NotificationsResult), 200)]
    [ProducesResponseType(typeof(ApiError), 401)]
    public async Task<ActionResult<AgentBoard.Application.Board.Dtos.NotificationsResult>> List(
        [FromQuery] int limit = 20, [FromQuery] int offset = 0,
        [FromQuery(Name = "unread_only")] bool unreadOnly = false,
        CancellationToken ct = default)
    {
        var uid = CurrentUser.UserId;
        if (uid is null) return Problem(401, "authentication required");
        return Ok(await Provider.ListNotificationsAsync(uid.Value, limit, offset, unreadOnly, ct));
    }

    [HttpGet("unread-count")]
    [ProducesResponseType(typeof(object), 200)]
    [ProducesResponseType(typeof(ApiError), 401)]
    public async Task<ActionResult<object>> UnreadCount(CancellationToken ct = default)
    {
        var uid = CurrentUser.UserId;
        if (uid is null) return Problem(401, "authentication required");
        return Ok(new { count = await Provider.GetUnreadNotificationCountAsync(uid.Value, ct) });
    }
}
