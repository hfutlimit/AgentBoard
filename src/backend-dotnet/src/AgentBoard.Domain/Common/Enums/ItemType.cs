// SPDX-License-Identifier: MIT
namespace AgentBoard.Domain.Common.Enums;

/// <summary>
/// Work item type — must match the FastAPI /api/meta types enum
/// (kept in lock-step via the OpenAPI contract freeze).
/// </summary>
public enum ItemType
{
    Task = 1,
    Bug = 2,
}
