// SPDX-License-Identifier: MIT
namespace AgentBoard.Domain.Common;

/// <summary>
/// Marker for entities that carry audit fields. The
/// <c>AuditFieldsInterceptor</c> fills these on every save.
/// </summary>
public interface IAuditableEntity
{
    DateTime CreatedAt { get; }
    DateTime UpdatedAt { get; }
    int? CreatedBy { get; }
    int? UpdatedBy { get; }
}
