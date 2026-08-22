// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Entities;

/// <summary>
/// API operation audit log. Maps to the <c>audit_logs</c> table.
/// </summary>
public sealed class AuditLog : Entity
{
    public int? UserId { get; set; }
    public string Action { get; set; } = string.Empty;
    public string EntityType { get; set; } = string.Empty;
    public int? EntityId { get; set; }
    public string Method { get; set; } = string.Empty;
    public string Path { get; set; } = string.Empty;
    public string? IpAddress { get; set; }
    public string? UserAgent { get; set; }
    public string? RequestBody { get; set; }
    public int? ResponseStatus { get; set; }
    public int? DurationMs { get; set; }
    public DateTime CreatedAt { get; set; }
}
