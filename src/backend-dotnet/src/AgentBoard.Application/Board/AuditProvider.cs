// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
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
        string? entityType, int? entityId, int? userId, string? action,
        int limit, int offset, CancellationToken ct = default)
    {
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
