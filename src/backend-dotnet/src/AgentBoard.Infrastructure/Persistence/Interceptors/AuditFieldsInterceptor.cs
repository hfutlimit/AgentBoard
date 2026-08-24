// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Domain.Common;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.ChangeTracking;
using Microsoft.EntityFrameworkCore.Diagnostics;

namespace AgentBoard.Infrastructure.Persistence.Interceptors;

/// <summary>
/// Stamps <c>CreatedAt / CreatedBy / UpdatedAt / UpdatedBy</c> on every
/// <see cref="IAuditableEntity"/> on save. <c>Created*</c> is only set on
/// insert; <c>Updated*</c> is set on every write. No-op for entities that
/// don't implement the interface.
/// </summary>
public sealed class AuditFieldsInterceptor : SaveChangesInterceptor
{
    private readonly IClock _clock;
    private readonly ICurrentUser _current;

    public AuditFieldsInterceptor(IClock clock, ICurrentUser current)
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
            if (entry.Entity is not IAuditableEntity auditable) continue;
            if (entry.State == EntityState.Added)
            {
                SetProperty(auditable, nameof(IAuditableEntity.CreatedAt), now);
                SetProperty(auditable, nameof(IAuditableEntity.CreatedBy), uid);
            }
            if (entry.State is EntityState.Added or EntityState.Modified)
            {
                SetProperty(auditable, nameof(IAuditableEntity.UpdatedAt), now);
                SetProperty(auditable, nameof(IAuditableEntity.UpdatedBy), uid);
            }
        }
    }

    private static void SetProperty(IAuditableEntity target, string propertyName, object? value)
    {
        var prop = target.GetType().GetProperty(propertyName);
        prop?.SetValue(target, value);
    }
}
