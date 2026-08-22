// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Entities;

/// <summary>
/// Read-only projection of the FastAPI-owned <c>projects</c> table. Mapped
/// to the existing Alembic-managed schema; EF never emits DDL for this table
/// (see <c>ReadOnlyConfiguration</c> → <c>ExcludeFromMigrations</c>).
/// </summary>
public sealed class Project : Entity
{
    public string Name { get; set; } = string.Empty;
    public string? Key { get; set; }
    public string Description { get; set; } = string.Empty;
    public bool IsPrivate { get; set; }
    public DateTime CreatedAt { get; set; }
    public bool IsArchived { get; set; }
    public DateTime? ArchivedAt { get; set; }
    public int? ArchivedBy { get; set; }
}
