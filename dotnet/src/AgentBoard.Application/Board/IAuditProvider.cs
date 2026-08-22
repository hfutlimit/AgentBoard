// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Domain.Entities;

namespace AgentBoard.Application.Board;

/// <summary>Audit log queries. Mirrors FastAPI audit-logs router.</summary>
public interface IAuditProvider : IProvider
{
    Task<IReadOnlyList<AuditLog>> ListAuditLogsAsync(
        string? entityType, int? entityId, int? userId, string? action,
        int limit, int offset, CancellationToken ct = default);
}
