// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Entities;

/// <summary>
/// Task dependency relationship. Maps to the <c>task_dependencies</c> table.
/// </summary>
public sealed class TaskDependency : Entity
{
    public int TaskId { get; set; }
    public int DependsOnId { get; set; }
    public string DependencyType { get; set; } = "blocks";
    public DateTime CreatedAt { get; set; }
}
