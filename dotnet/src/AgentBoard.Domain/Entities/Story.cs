// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Entities;

/// <summary>
/// Read-only projection of the FastAPI-owned <c>stories</c> table.
/// </summary>
public sealed class Story : Entity
{
    public int EpicId { get; set; }
    public string Title { get; set; } = string.Empty;
    public string Description { get; set; } = string.Empty;
    public string Status { get; set; } = "backlog";
    public bool NeedsDesign { get; set; } = true;
    public int? ReviewerId { get; set; }
    public int ReviewRound { get; set; }
    public bool InKanban { get; set; }
    public DateTime CreatedAt { get; set; }
}
