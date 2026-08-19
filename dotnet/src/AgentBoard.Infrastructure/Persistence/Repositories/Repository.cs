// SPDX-License-Identifier: MIT
using System.Linq.Expressions;
using AgentBoard.Application.Abstractions;
using AgentBoard.Domain.Common;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.ChangeTracking;

namespace AgentBoard.Infrastructure.Persistence.Repositories;

/// <summary>
/// Generic EF Core implementation of <see cref="IRepository{T}"/>. Provides
/// the common CRUD surface; domain-specific repositories extend this and
/// expose projection-based queries (no <c>Include</c>).
/// </summary>
public abstract class Repository<T> : IRepository<T> where T : Entity
{
    /// <summary>Concrete DbContext. Subclasses expose typed DbSets.</summary>
    protected AppDbContext Db { get; }

    /// <summary>Concrete DbSet for the entity. Subclasses bind this once.</summary>
    protected abstract DbSet<T> Set { get; }

    protected Repository(AppDbContext db)
    {
        Db = db ?? throw new ArgumentNullException(nameof(db));
    }

    public virtual async Task<T?> GetByIdAsync(int id, CancellationToken ct = default) =>
        await Set.AsNoTracking().FirstOrDefaultAsync(e => e.Id == id, ct);

    public virtual async Task<IReadOnlyList<T>> ListAsync(
        Expression<Func<T, bool>>? predicate = null, CancellationToken ct = default)
    {
        var q = Set.AsNoTracking().AsQueryable();
        if (predicate is not null) q = q.Where(predicate);
        return await q.ToListAsync(ct);
    }

    public virtual async Task<long> CountAsync(
        Expression<Func<T, bool>>? predicate = null, CancellationToken ct = default)
    {
        var q = Set.AsNoTracking().AsQueryable();
        if (predicate is not null) q = q.Where(predicate);
        return await q.LongCountAsync(ct);
    }

    public virtual async Task<bool> ExistsAsync(
        Expression<Func<T, bool>> predicate, CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(predicate);
        return await Set.AsNoTracking().AnyAsync(predicate, ct);
    }

    public virtual async Task<T> AddAsync(T entity, CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(entity);
        var entry = await Set.AddAsync(entity, ct);
        return entry.Entity;
    }

    public virtual async Task AddRangeAsync(IEnumerable<T> entities, CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(entities);
        await Set.AddRangeAsync(entities, ct);
    }

    public virtual void Update(T entity)
    {
        ArgumentNullException.ThrowIfNull(entity);
        Db.Entry(entity).State = EntityState.Modified;
    }

    public virtual void UpdateRange(IEnumerable<T> entities)
    {
        ArgumentNullException.ThrowIfNull(entities);
        foreach (var e in entities) Db.Entry(e).State = EntityState.Modified;
    }

    public virtual void Remove(T entity)
    {
        ArgumentNullException.ThrowIfNull(entity);
        Db.Entry(entity).State = EntityState.Deleted;
    }

    public virtual void RemoveRange(IEnumerable<T> entities)
    {
        ArgumentNullException.ThrowIfNull(entities);
        foreach (var e in entities) Db.Entry(e).State = EntityState.Deleted;
    }

    public virtual void Attach(T entity)
    {
        ArgumentNullException.ThrowIfNull(entity);
        Db.Entry(entity).State = EntityState.Unchanged;
    }
}
