// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board.Dtos;
using AgentBoard.Domain.Common;

namespace AgentBoard.Application.Board;

/// <summary>Notification write operations. Mirrors FastAPI notifications router.</summary>
public sealed class NotificationProvider : INotificationProvider
{
    private readonly INotificationRepository _notifications;
    private readonly IUnitOfWork _uow;

    public NotificationProvider(INotificationRepository notifications, IUnitOfWork uow)
    {
        _notifications = notifications ?? throw new ArgumentNullException(nameof(notifications));
        _uow = uow ?? throw new ArgumentNullException(nameof(uow));
    }

    public async Task<bool> MarkReadAsync(int notifId, int userId, CancellationToken ct = default)
    {
        var items = await _notifications.ListAsync(n => n.Id == notifId && n.UserId == userId, ct);
        var notif = items.FirstOrDefault();
        if (notif is null) return false;
        notif.IsRead = true;
        _notifications.Update(notif);
        await _uow.SaveChangesAsync(ct);
        return true;
    }

    public async Task<int> MarkAllReadAsync(int userId, CancellationToken ct = default)
    {
        var items = await _notifications.ListAsync(n => n.UserId == userId && !n.IsRead, ct);
        foreach (var n in items)
        {
            n.IsRead = true;
            _notifications.Update(n);
        }
        if (items.Count > 0) await _uow.SaveChangesAsync(ct);
        return items.Count;
    }

    public async Task<bool> DeleteNotificationAsync(int notifId, int userId, CancellationToken ct = default)
    {
        var items = await _notifications.ListAsync(n => n.Id == notifId && n.UserId == userId, ct);
        var notif = items.FirstOrDefault();
        if (notif is null) return false;
        _notifications.Remove(notif);
        await _uow.SaveChangesAsync(ct);
        return true;
    }
}
