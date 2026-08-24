// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Entities;

/// <summary>Read-only projection of the FastAPI-owned <c>project_members</c> table.</summary>
public sealed class ProjectMember : Entity
{
    public int ProjectId { get; set; }
    public int UserId { get; set; }
    public string Role { get; set; } = "member";
    public DateTime JoinedAt { get; set; }
}
