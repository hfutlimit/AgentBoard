// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Entities;

/// <summary>Read-only projection of the FastAPI-owned <c>notifications</c> table.</summary>
public sealed class Notification : Entity
{
    public int UserId { get; set; }
    public string Type { get; set; } = string.Empty;
    public string Title { get; set; } = string.Empty;
    public string Content { get; set; } = string.Empty;
    public bool IsRead { get; set; }
    public string? Link { get; set; }
    public DateTime CreatedAt { get; set; }
}
