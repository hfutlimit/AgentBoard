// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Entities;

/// <summary>
/// Document entity. Maps to <c>documents</c> table.
/// Types: memory, plan, knowledge, design.
/// Statuses: draft, in_review, approved, cancelled.
/// </summary>
public sealed class Document : Entity
{
    public int ProjectId { get; set; }
    public int? EpicId { get; set; }
    public int? StoryId { get; set; }
    public int? FolderId { get; set; }
    public string Title { get; set; } = string.Empty;
    public string Content { get; set; } = string.Empty;
    public string Type { get; set; } = "plan";
    public string Status { get; set; } = "draft";
    public int? AuthorId { get; set; }
    public int CurrentRevisionId { get; set; }
    public int CurrentRevisionNumber { get; set; }
    public DateTime CreatedAt { get; set; }
    public DateTime UpdatedAt { get; set; }
}
