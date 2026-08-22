// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Entities;

/// <summary>
/// Document folder (hierarchical). Maps to <c>document_folders</c> table.
/// </summary>
public sealed class DocumentFolder : Entity
{
    public int ProjectId { get; set; }
    public int? ParentId { get; set; }
    public string Name { get; set; } = string.Empty;
    public DateTime CreatedAt { get; set; }
    public DateTime UpdatedAt { get; set; }
}
