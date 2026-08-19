// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Domain.Common;
using AgentBoard.Domain.Identity;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.ChangeTracking;
using Microsoft.EntityFrameworkCore.Diagnostics;

namespace AgentBoard.Infrastructure.Persistence;

/// <summary>
/// Single DbContext for the entire .NET BFF. Entity configurations live
/// in <see cref="Configurations"/>. Audit / soft-delete / domain-event
/// dispatch is handled by SaveChanges interceptors registered in
/// <c>AgentBoard.Infrastructure.DependencyInjection</c>.
/// </summary>
public sealed class AppDbContext : DbContext, IDbContext, IUnitOfWork
{
    public AppDbContext(DbContextOptions<AppDbContext> options) : base(options) { }

    public DbSet<User> Users => Set<User>();

    public new Task<int> SaveChangesAsync(CancellationToken ct = default) =>
        base.SaveChangesAsync(ct);

    public void Detach<TEntity>(TEntity entity) where TEntity : class
    {
        ArgumentNullException.ThrowIfNull(entity);
        Entry(entity).State = EntityState.Detached;
    }

    public bool HasChanges => ChangeTracker.HasChanges();

    public Task<bool> CanConnectAsync(CancellationToken ct = default) =>
        Database.CanConnectAsync(ct);

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        ArgumentNullException.ThrowIfNull(modelBuilder);
        modelBuilder.ApplyConfigurationsFromAssembly(typeof(AppDbContext).Assembly);
        base.OnModelCreating(modelBuilder);
    }
}
