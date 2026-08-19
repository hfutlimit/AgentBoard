// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Common;

/// <summary>Standard paged result envelope, matches FastAPI's paginated list shape.</summary>
public sealed record PagedResponse<T>(
    IReadOnlyList<T> Items,
    int Page,
    int PageSize,
    long Total);
