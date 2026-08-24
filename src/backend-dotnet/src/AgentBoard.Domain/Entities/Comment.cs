// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Entities;

/// <summary>
/// Read-only projection of the FastAPI-owned <c>comments</c> table.
/// A comment attaches to exactly one of Task / Story / Epic (whichever id is non-null).
/// </summary>
public sealed class Comment : Entity
{
    public int? TaskId { get; set; }
    public int? StoryId { get; set; }
    public int? EpicId { get; set; }
    public string Author { get; set; } = string.Empty;
    public string Content { get; set; } = string.Empty;
    public DateTime CreatedAt { get; set; }
    public DateTime UpdatedAt { get; set; }
}
