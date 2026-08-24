// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Entities;

/// <summary>
/// Read-only projection of the FastAPI-owned <c>epics</c> table.
/// </summary>
public sealed class Epic : Entity
{
    public int ProjectId { get; set; }
    public string Title { get; set; } = string.Empty;
    public string Description { get; set; } = "";
    public string Status { get; set; } = "backlog";
    public DateTime CreatedAt { get; set; }
}
