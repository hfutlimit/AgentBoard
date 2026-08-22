// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Entities;

/// <summary>
/// Comment on a document. Maps to <c>document_comments</c> table.
/// </summary>
public sealed class DocumentComment : Entity
{
    public int DocumentId { get; set; }
    public int? AuthorId { get; set; }
    public string Author { get; set; } = string.Empty;
    public string Content { get; set; } = string.Empty;
    public DateTime CreatedAt { get; set; }
    public DateTime UpdatedAt { get; set; }
}
