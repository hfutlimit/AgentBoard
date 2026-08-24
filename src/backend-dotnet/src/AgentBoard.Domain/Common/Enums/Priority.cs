// SPDX-License-Identifier: MIT
namespace AgentBoard.Domain.Common.Enums;

/// <summary>Five-level work item priority, must match FastAPI /api/meta priorities.</summary>
public enum Priority
{
    Lowest = 1,
    Low = 2,
    Medium = 3,
    High = 4,
    Highest = 5,
}
