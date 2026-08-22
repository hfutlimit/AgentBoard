// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Entities;

/// <summary>
/// Read-only projection of the FastAPI-owned <c>tasks</c> table. Named
/// <c>TaskItem</c> to avoid colliding with <see cref="System.Threading.Tasks.Task"/>.
/// </summary>
public sealed class TaskItem : Entity
{
    public int ProjectId { get; set; }
    public int? StoryId { get; set; }
    public int? SprintId { get; set; }
    public string Type { get; set; } = "dev";
    public string Title { get; set; } = string.Empty;
    public string Status { get; set; } = "todo";
    public string Priority { get; set; } = "medium";
    public string? StatusReason { get; set; }
    public string Description { get; set; } = string.Empty;
    public string Spec { get; set; } = string.Empty;
    public int? AssigneeId { get; set; }
    public DateTime? DueDate { get; set; }
    public string Labels { get; set; } = "[]";
    public double? Estimate { get; set; }
    public string NeededCapabilities { get; set; } = "[]";
    public int? Complexity { get; set; }
    public string DomainTags { get; set; } = "[]";
    public string AssignmentMode { get; set; } = "claim";
    public int? ReviewerId { get; set; }
    public int ReviewRound { get; set; }
    public string? PreviousStatus { get; set; }
    public DateTime CreatedAt { get; set; }
    public DateTime UpdatedAt { get; set; }
}
