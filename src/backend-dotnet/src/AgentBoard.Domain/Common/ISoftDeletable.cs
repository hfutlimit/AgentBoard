// SPDX-License-Identifier: MIT
namespace AgentBoard.Domain.Common;

/// <summary>
/// Marker for entities that support soft deletion. The
/// <c>SoftDeleteInterceptor</c> converts <c>Delete</c> operations into
/// <c>Update DeletedAt = UtcNow</c> and a global query filter hides them.
/// </summary>
public interface ISoftDeletable
{
    DateTime? DeletedAt { get; }
    int? DeletedBy { get; }
}
