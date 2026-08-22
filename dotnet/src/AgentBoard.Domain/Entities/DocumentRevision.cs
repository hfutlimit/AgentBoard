// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Entities;

/// <summary>
/// Immutable document content snapshot. Maps to <c>document_revisions</c> table.
/// </summary>
public sealed class DocumentRevision : Entity
{
    public int DocumentId { get; set; }
    public int RevisionNumber { get; set; }
    public int? AuthorId { get; set; }
    public string Author { get; set; } = string.Empty;
    public string Content { get; set; } = string.Empty;
    public string? ChangeNote { get; set; }
    public DateTime CreatedAt { get; set; }
}
