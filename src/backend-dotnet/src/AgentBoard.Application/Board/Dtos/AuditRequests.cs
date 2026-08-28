// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Board.Dtos;

/// <summary>Query parameters for <c>GET /api/audit-logs</c>. All filters optional.</summary>
public sealed record ListAuditLogsQuery(
    string? EntityType,
    int? EntityId,
    int? UserId,
    string? Action,
    int? Limit,
    int? Offset);
