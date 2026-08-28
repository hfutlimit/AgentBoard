// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board.Dtos;
using AgentBoard.Domain.Entities;

namespace AgentBoard.Application.Board;

/// <summary>Audit log queries. Mirrors FastAPI audit-logs router.</summary>
public interface IAuditProvider : IProvider
{
    Task<IReadOnlyList<AuditLog>> ListAuditLogsAsync(
        ListAuditLogsQuery query, CancellationToken ct = default);
}
