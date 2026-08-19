// SPDX-License-Identifier: MIT
using System.Linq.Expressions;
using AgentBoard.Domain.Common;

namespace AgentBoard.Application.Abstractions;

/// <summary>
/// Generic read/write contract for an aggregate. Domain-specific queries
/// belong on the entity-specific <c>I{Domain}Repository</c> interface that
/// extends this one. The Application layer never depends on EF Core, so the
/// return type is the domain entity, not <c>IQueryable&lt;T&gt;</c>.
/// </summary>
public interface IRepository<T> where T : Entity
{
    Task<T?> GetByIdAsync(int id, CancellationToken ct = default);
    Task<IReadOnlyList<T>> ListAsync(Expression<Func<T, bool>>? predicate = null, CancellationToken ct = default);
    Task<long> CountAsync(Expression<Func<T, bool>>? predicate = null, CancellationToken ct = default);
    Task<bool> ExistsAsync(Expression<Func<T, bool>> predicate, CancellationToken ct = default);

    Task<T> AddAsync(T entity, CancellationToken ct = default);
    Task AddRangeAsync(IEnumerable<T> entities, CancellationToken ct = default);
    void Attach(T entity);
    void Update(T entity);
    void UpdateRange(IEnumerable<T> entities);
    void Remove(T entity);
    void RemoveRange(IEnumerable<T> entities);
}
