// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Base;
using AgentBoard.Api.Api.Common;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Notifications;

/// <summary>Notification reads + writes. Mirrors FastAPI <c>/api/notifications</c>.</summary>
[ApiController]
[Route("api/notifications")]
[Produces("application/json")]
public sealed class NotificationsController : BaseController
{
    private readonly IBoardProvider _boardProvider;
    private readonly INotificationProvider _notificationProvider;

    public NotificationsController(
        IBoardProvider boardProvider,
        INotificationProvider notificationProvider,
        ICurrentUser current) : base(current)
    {
        _boardProvider = boardProvider ?? throw new ArgumentNullException(nameof(boardProvider));
        _notificationProvider = notificationProvider ?? throw new ArgumentNullException(nameof(notificationProvider));
    }

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
        return Ok(await _boardProvider.ListNotificationsAsync(uid.Value, limit, offset, unreadOnly, ct));
    }

    [HttpGet("unread-count")]
    [ProducesResponseType(typeof(object), 200)]
    [ProducesResponseType(typeof(ApiError), 401)]
    public async Task<ActionResult<object>> UnreadCount(CancellationToken ct = default)
    {
        var uid = CurrentUser.UserId;
        if (uid is null) return Problem(401, "authentication required");
        return Ok(new { count = await _boardProvider.GetUnreadNotificationCountAsync(uid.Value, ct) });
    }

    [HttpPost("{notifId:int}/read")]
    [ProducesResponseType(typeof(object), 200)]
    [ProducesResponseType(typeof(ApiError), 401)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult> MarkRead(int notifId, CancellationToken ct)
    {
        var uid = CurrentUser.UserId;
        if (uid is null) return Problem(401, "authentication required");
        var ok = await _notificationProvider.MarkReadAsync(notifId, uid.Value, ct);
        return ok ? Ok(new { ok = true }) : NotFound(new ApiError($"notification {notifId} not found"));
    }

    [HttpPost("read-all")]
    [ProducesResponseType(typeof(object), 200)]
    [ProducesResponseType(typeof(ApiError), 401)]
    public async Task<ActionResult> MarkAllRead(CancellationToken ct)
    {
        var uid = CurrentUser.UserId;
        if (uid is null) return Problem(401, "authentication required");
        var count = await _notificationProvider.MarkAllReadAsync(uid.Value, ct);
        return Ok(new { count });
    }

    [HttpDelete("{notifId:int}")]
    [ProducesResponseType(typeof(object), 200)]
    [ProducesResponseType(typeof(ApiError), 401)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult> Delete(int notifId, CancellationToken ct)
    {
        var uid = CurrentUser.UserId;
        if (uid is null) return Problem(401, "authentication required");
        var ok = await _notificationProvider.DeleteNotificationAsync(notifId, uid.Value, ct);
        return ok ? Ok(new { ok = true }) : NotFound(new ApiError($"notification {notifId} not found"));
    }
}
