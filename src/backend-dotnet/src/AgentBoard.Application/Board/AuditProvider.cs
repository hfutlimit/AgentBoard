// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board.Dtos;
using AgentBoard.Domain.Entities;

namespace AgentBoard.Application.Board;

/// <summary>Audit log queries. Mirrors FastAPI audit-logs router.</summary>
public sealed class AuditProvider : IAuditProvider
{
    private readonly IAuditLogRepository _auditLogs;

    public AuditProvider(IAuditLogRepository auditLogs)
    {
        _auditLogs = auditLogs ?? throw new ArgumentNullException(nameof(auditLogs));
    }

    public async Task<IReadOnlyList<AuditLog>> ListAuditLogsAsync(
        ListAuditLogsQuery query, CancellationToken ct = default)
    {
        var entityType = query.EntityType;
        var entityId = query.EntityId;
        var userId = query.UserId;
        var action = query.Action;
        var limit = query.Limit ?? 100;
        var offset = query.Offset ?? 0;
        var items = await _auditLogs.ListAsync(ct: ct);

        if (entityType is not null)
            items = items.Where(a => a.EntityType == entityType).ToList();
        if (entityId is not null)
            items = items.Where(a => a.EntityId == entityId).ToList();
        if (userId is not null)
            items = items.Where(a => a.UserId == userId).ToList();
        if (action is not null)
            items = items.Where(a => a.Action == action).ToList();

        return items
            .OrderByDescending(a => a.CreatedAt)
            .Skip(offset)
            .Take(limit)
            .ToList();
    }
}
