// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Entities;

/// <summary>
/// Story status change history. Maps to <c>story_status_history</c> table.
/// </summary>
public sealed class StoryStatusHistory : Entity
{
    public int StoryId { get; set; }
    public string FromStatus { get; set; } = string.Empty;
    public string ToStatus { get; set; } = string.Empty;
    public int? ChangedBy { get; set; }
    public string? Reason { get; set; }
    public DateTime CreatedAt { get; set; }
}
