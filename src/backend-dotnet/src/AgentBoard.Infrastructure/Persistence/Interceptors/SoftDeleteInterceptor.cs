// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Domain.Common;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.ChangeTracking;
using Microsoft.EntityFrameworkCore.Diagnostics;

namespace AgentBoard.Infrastructure.Persistence.Interceptors;

/// <summary>
/// Converts <c>EntityState.Deleted</c> into a soft-delete on entities that
/// implement <see cref="ISoftDeletable"/>. The global query filter
/// (registered in <c>OnModelCreating</c> per-entity) hides them from reads.
///
/// Soft-deletes are **not** cascaded — relations must be deleted first by
/// application code, otherwise the foreign-key constraint fires.
/// </summary>
public sealed class SoftDeleteInterceptor : SaveChangesInterceptor
{
    private readonly IClock _clock;
    private readonly ICurrentUser _current;

    public SoftDeleteInterceptor(IClock clock, ICurrentUser current)
    {
        _clock = clock ?? throw new ArgumentNullException(nameof(clock));
        _current = current ?? throw new ArgumentNullException(nameof(current));
    }

    public override InterceptionResult<int> SavingChanges(
        DbContextEventData eventData, InterceptionResult<int> result)
    {
        Apply(eventData.Context);
        return base.SavingChanges(eventData, result);
    }

    public override ValueTask<InterceptionResult<int>> SavingChangesAsync(
        DbContextEventData eventData, InterceptionResult<int> result, CancellationToken ct = default)
    {
        Apply(eventData.Context);
        return base.SavingChangesAsync(eventData, result, ct);
    }

    private void Apply(DbContext? db)
    {
        if (db is null) return;
        var now = _clock.UtcNow;
        var uid = _current.UserId;
        foreach (EntityEntry entry in db.ChangeTracker.Entries())
        {
            if (entry.State != EntityState.Deleted) continue;
            if (entry.Entity is not ISoftDeletable soft) continue;

            // Flip state to Modified + stamp deleted_at.
            entry.State = EntityState.Modified;
            SetProperty(soft, nameof(ISoftDeletable.DeletedAt), now);
            SetProperty(soft, nameof(ISoftDeletable.DeletedBy), uid);
        }
    }

    private static void SetProperty(ISoftDeletable target, string propertyName, object? value)
    {
        var prop = target.GetType().GetProperty(propertyName);
        prop?.SetValue(target, value);
    }
}
