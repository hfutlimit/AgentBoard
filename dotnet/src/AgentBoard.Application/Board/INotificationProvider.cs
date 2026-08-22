// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;

namespace AgentBoard.Application.Board;

/// <summary>Notification write operations. Mirrors FastAPI notifications router.</summary>
public interface INotificationProvider : IProvider
{
    Task<bool> MarkReadAsync(int notifId, int userId, CancellationToken ct = default);
    Task<int> MarkAllReadAsync(int userId, CancellationToken ct = default);
    Task<bool> DeleteNotificationAsync(int notifId, int userId, CancellationToken ct = default);
}
