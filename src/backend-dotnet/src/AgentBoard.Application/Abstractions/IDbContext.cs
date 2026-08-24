// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Abstractions;

/// <summary>
/// Persistence-agnostic DbContext abstraction. The Application layer only
/// sees this interface; the EF Core implementation lives in Infrastructure.
/// Exposes the subset of operations that the Application layer actually
/// needs — full CRUD goes through <see cref="IRepository{T}"/>.
/// </summary>
public interface IDbContext
{
    /// <summary>Persists pending changes. Returns rows affected.</summary>
    Task<int> SaveChangesAsync(CancellationToken ct = default);

    /// <summary>Detaches an entity from the change tracker.</summary>
    void Detach<TEntity>(TEntity entity) where TEntity : class;

    /// <summary>True if at least one entity in the change tracker is dirty.</summary>
    bool HasChanges { get; }

    /// <summary>
    /// Cheap smoke-test for the underlying connection. Returns true when
    /// the database accepts a no-op round trip. The EF Core implementation
    /// issues <c>SELECT 1</c>; the in-memory implementation always returns
    /// true. This is the only IDbContext method that the API layer is
    /// allowed to call directly (the health endpoint is the only consumer).
    /// </summary>
    Task<bool> CanConnectAsync(CancellationToken ct = default);
}
