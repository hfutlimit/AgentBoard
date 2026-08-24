// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Entities;

/// <summary>
/// Sprint entity. Maps to the existing <c>sprints</c> table (Alembic-managed).
/// </summary>
public sealed class Sprint : Entity
{
    public int ProjectId { get; set; }
    public string Title { get; set; } = string.Empty;
    public string Goal { get; set; } = string.Empty;
    public string Status { get; set; } = "planning";
    public DateTime? StartDate { get; set; }
    public DateTime? EndDate { get; set; }
    public DateTime CreatedAt { get; set; }
}
