// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Common;
using AgentBoard.Domain.Entities;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata.Builders;

namespace AgentBoard.Infrastructure.Persistence.Configurations;

/// <summary>
/// Base mapping for read-only entities that mirror FastAPI/Alembic-owned
/// tables. The entities inherit <see cref="Entity"/> (for the generic
/// <c>IRepository&lt;T&gt;</c> contract) but the framework-owned columns
/// <c>row_version</c> and <c>DomainEvents</c> do not exist in the shared
/// schema, so they are ignored. The <c>ToTable(...)</c> overload with
/// <c>ExcludeFromMigrations</c> keeps EF from emitting DDL — the ADR rule
/// "shared tables are owned by Alembic".
/// </summary>
public abstract class ReadOnlyConfiguration<T> : IEntityTypeConfiguration<T>
    where T : Entity
{
    public void Configure(EntityTypeBuilder<T> b)
    {
        b.HasKey(e => e.Id);
        b.Property(e => e.Id).HasColumnName("id").ValueGeneratedOnAdd();
        b.Ignore(e => e.RowVersion);
        b.Ignore(e => e.DomainEvents);
        ConfigureEntity(b);
    }

    /// <summary>Subclasses bind the table name + columns here.</summary>
    protected abstract void ConfigureEntity(EntityTypeBuilder<T> b);
}

public sealed class ProjectConfiguration : ReadOnlyConfiguration<Project>
{
    protected override void ConfigureEntity(EntityTypeBuilder<Project> b)
    {
        b.ToTable("projects", t => t.ExcludeFromMigrations());
        b.Property(e => e.Name).HasColumnName("name").HasMaxLength(200).IsRequired();
        b.Property(e => e.Key).HasColumnName("key").HasMaxLength( 20);
        b.Property(e => e.Description).HasColumnName("description");
        b.Property(e => e.IsPrivate).HasColumnName("is_private").HasDefaultValue(false);
        b.Property(e => e.CreatedAt).HasColumnName("created_at");
        b.Property(e => e.IsArchived).HasColumnName("is_archived").HasDefaultValue(false);
        b.Property(e => e.ArchivedAt).HasColumnName("archived_at");
        b.Property(e => e.ArchivedBy).HasColumnName("archived_by");
        b.HasIndex(e => e.Key).IsUnique().HasFilter("[key] IS NOT NULL");
    }
}

public sealed class EpicConfiguration : ReadOnlyConfiguration<Epic>
{
    protected override void ConfigureEntity(EntityTypeBuilder<Epic> b)
    {
        b.ToTable("epics", t => t.ExcludeFromMigrations());
        b.Property(e => e.ProjectId).HasColumnName("project_id");
        b.Property(e => e.Title).HasColumnName("title").HasMaxLength(300).IsRequired();
        b.Property(e => e.Description).HasColumnName("description");
        b.Property(e => e.Status).HasColumnName("status").HasMaxLength(40).HasDefaultValue("backlog");
        b.Property(e => e.CreatedAt).HasColumnName("created_at");
        b.HasIndex(e => e.ProjectId);
    }
}

public sealed class StoryConfiguration : ReadOnlyConfiguration<Story>
{
    protected override void ConfigureEntity(EntityTypeBuilder<Story> b)
    {
        b.ToTable("stories", t => t.ExcludeFromMigrations());
        b.Property(e => e.EpicId).HasColumnName("epic_id");
        b.Property(e => e.Title).HasColumnName("title").HasMaxLength( 300).IsRequired();
        b.Property(e => e.Description).HasColumnName("description");
        b.Property(e => e.Status).HasColumnName("status").HasMaxLength(40).HasDefaultValue("backlog");
        b.Property(e => e.NeedsDesign).HasColumnName("needs_design").HasDefaultValue(true);
        b.Property(e => e.ReviewerId).HasColumnName("reviewer_id");
        b.Property(e => e.ReviewRound).HasColumnName("review_round").HasDefaultValue(0);
        b.Property(e => e.InKanban).HasColumnName("in_kanban").HasDefaultValue(false);
        b.Property(e => e.CreatedAt).HasColumnName("created_at");
        b.HasIndex(e => e.EpicId);
    }
}

public sealed class TaskItemConfiguration : ReadOnlyConfiguration<TaskItem>
{
    protected override void ConfigureEntity(EntityTypeBuilder<TaskItem> b)
    {
        b.ToTable("tasks", t => t.ExcludeFromMigrations());
        b.Property(e => e.ProjectId).HasColumnName("project_id");
        b.Property(e => e.StoryId).HasColumnName("story_id");
        b.Property(e => e.SprintId).HasColumnName("sprint_id");
        b.Property(e => e.Type).HasColumnName("type").HasMaxLength(10).HasDefaultValue("dev");
        b.Property(e => e.Title).HasColumnName("title").HasMaxLength(300).IsRequired();
        b.Property(e => e.Status).HasColumnName("status").HasMaxLength(40).HasDefaultValue("todo");
        b.Property(e => e.Priority).HasColumnName("priority").HasMaxLength(10).HasDefaultValue("medium");
        b.Property(e => e.StatusReason).HasColumnName("status_reason").HasMaxLength(40);
        b.Property(e => e.Description).HasColumnName("description");
        b.Property(e => e.Spec).HasColumnName("spec");
        b.Property(e => e.AssigneeId).HasColumnName("assignee_id");
        b.Property(e => e.DueDate).HasColumnName("due_date");
        b.Property(e => e.Labels).HasColumnName("labels");
        b.Property(e => e.Estimate).HasColumnName("estimate");
        b.Property(e => e.NeededCapabilities).HasColumnName("needed_capabilities");
        b.Property(e => e.Complexity).HasColumnName("complexity");
        b.Property(e => e.DomainTags).HasColumnName("domain_tags");
        b.Property(e => e.AssignmentMode).HasColumnName("assignment_mode").HasMaxLength(20).HasDefaultValue("claim");
        b.Property(e => e.ReviewerId).HasColumnName("reviewer_id");
        b.Property(e => e.ReviewRound).HasColumnName("review_round").HasDefaultValue(0);
        b.Property(e => e.PreviousStatus).HasColumnName("previous_status").HasMaxLength(40);
        b.Property(e => e.CreatedAt).HasColumnName("created_at");
        b.Property(e => e.UpdatedAt).HasColumnName("updated_at");
        b.HasIndex(e => e.ProjectId);
        b.HasIndex(e => e.StoryId);
        b.HasIndex(e => e.SprintId);
        b.HasIndex(e => e.AssigneeId);
    }
}

public sealed class CommentConfiguration : ReadOnlyConfiguration<Comment>
{
    protected override void ConfigureEntity(EntityTypeBuilder<Comment> b)
    {
        b.ToTable("comments", t => t.ExcludeFromMigrations());
        b.Property(e => e.TaskId).HasColumnName("task_id");
        b.Property(e => e.StoryId).HasColumnName("story_id");
        b.Property(e => e.EpicId).HasColumnName("epic_id");
        b.Property(e => e.Author).HasColumnName("author").HasMaxLength(100).IsRequired();
        b.Property(e => e.Content).HasColumnName("content").IsRequired();
        b.Property(e => e.CreatedAt).HasColumnName("created_at");
        b.Property(e => e.UpdatedAt).HasColumnName("updated_at");
        b.HasIndex(e => e.TaskId);
        b.HasIndex(e => e.StoryId);
        b.HasIndex(e => e.EpicId);
    }
}

public sealed class ProjectMemberConfiguration : ReadOnlyConfiguration<ProjectMember>
{
    protected override void ConfigureEntity(EntityTypeBuilder<ProjectMember> b)
    {
        b.ToTable("project_members", t => t.ExcludeFromMigrations());
        b.Property(e => e.ProjectId).HasColumnName("project_id").IsRequired();
        b.Property(e => e.UserId).HasColumnName("user_id").IsRequired();
        b.Property(e => e.Role).HasColumnName("role").HasMaxLength(20).HasDefaultValue("member");
        b.Property(e => e.JoinedAt).HasColumnName("joined_at");
        b.HasIndex(e => e.ProjectId);
        b.HasIndex(e => e.UserId);
    }
}

public sealed class NotificationConfiguration : ReadOnlyConfiguration<Notification>
{
    protected override void ConfigureEntity(EntityTypeBuilder<Notification> b)
    {
        b.ToTable("notifications", t => t.ExcludeFromMigrations());
        b.Property(e => e.UserId).HasColumnName("user_id").IsRequired();
        b.Property(e => e.Type).HasColumnName("type").HasMaxLength(30).IsRequired();
        b.Property(e => e.Title).HasColumnName("title").HasMaxLength(300).IsRequired();
        b.Property(e => e.Content).HasColumnName("content");
        b.Property(e => e.IsRead).HasColumnName("is_read").HasDefaultValue(false);
        b.Property(e => e.Link).HasColumnName("link").HasMaxLength(500);
        b.Property(e => e.CreatedAt).HasColumnName("created_at");
        b.HasIndex(e => e.UserId);
    }
}
