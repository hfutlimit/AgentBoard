// SPDX-License-Identifier: MIT
namespace AgentBoard.Domain.Common.Enums;

/// <summary>Sprint lifecycle states, must match FastAPI /api/meta sprint_statuses.</summary>
public enum SprintStatus
{
    Planned = 1,
    Active = 2,
    Completed = 3,
}
