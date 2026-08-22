// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Domain.Common;
using AgentBoard.Domain.Entities;
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
    public DbSet<Project> Projects => Set<Project>();
    public DbSet<Epic> Epics => Set<Epic>();
    public DbSet<Story> Stories => Set<Story>();
    public DbSet<TaskItem> Tasks => Set<TaskItem>();
    public DbSet<Comment> Comments => Set<Comment>();
    public DbSet<ProjectMember> ProjectMembers => Set<ProjectMember>();
    public DbSet<Notification> Notifications => Set<Notification>();
    public DbSet<Sprint> Sprints => Set<Sprint>();
    public DbSet<Attachment> Attachments => Set<Attachment>();
    public DbSet<AuditLog> AuditLogs => Set<AuditLog>();
    public DbSet<TaskDependency> TaskDependencies => Set<TaskDependency>();
    public DbSet<WebhookConfig> WebhookConfigs => Set<WebhookConfig>();
    public DbSet<ApiKey> ApiKeys => Set<ApiKey>();
    public DbSet<Document> Documents => Set<Document>();
    public DbSet<DocumentRevision> DocumentRevisions => Set<DocumentRevision>();
    public DbSet<DocumentFolder> DocumentFolders => Set<DocumentFolder>();
    public DbSet<DocumentComment> DocumentComments => Set<DocumentComment>();
    public DbSet<StoryStatusHistory> StoryStatusHistories => Set<StoryStatusHistory>();
    public DbSet<TaskStatusHistory> TaskStatusHistories => Set<TaskStatusHistory>();

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
