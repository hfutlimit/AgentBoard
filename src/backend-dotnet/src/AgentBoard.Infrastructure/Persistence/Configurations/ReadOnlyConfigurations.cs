// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Common;
using AgentBoard.Domain.Entities;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata.Builders;

namespace AgentBoard.Infrastructure.Persistence.Configurations;

/// <summary>
/// Base mapping for entities that mirror FastAPI/Alembic-owned tables.
/// The entities inherit <see cref="Entity"/> (for the generic
/// <c>IRepository&lt;T&gt;</c> contract) but the framework-owned columns
/// <c>row_version</c> and <c>DomainEvents</c> do not exist in the shared
/// schema, so they are ignored.
///
/// Schema ownership (dual-stack ADR):
///   - Production: shared MariaDB schema is owned by the Python Alembic
///     operator; .NET BFF never runs <c>Database.Migrate</c> or
///     <c>EnsureCreated</c> on prod (gated by
///     <c>Program.cs:134</c> env check). Do NOT run
///     <c>dotnet ef migrations add</c> for these entities — the resulting
///     DDL would drift from the Alembic source of truth.
///   - Dev / Testing: the .NET BFF boots a SQLite shadow database and
///     calls <c>EnsureCreated</c>; the resulting tables MUST be kept in
///     lock-step with the FastAPI schema (see <c>openspec/changes/
///     dual-stack-bff-restructure/schema-ownership.md</c>). Whenever you
///     add a new <c>ReadOnlyConfiguration</c> here, mirror the change in
///     the FastAPI alembic migration on the same day.
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
		b.ToTable("projects");
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

public sealed class AgentScheduleConfiguration : ReadOnlyConfiguration<AgentSchedule>
{
	protected override void ConfigureEntity(EntityTypeBuilder<AgentSchedule> b)
	{
		b.ToTable("agent_schedules");
		b.Property(e => e.ProjectId).HasColumnName("project_id");
		b.Property(e => e.Title).HasColumnName("title").HasMaxLength(300).IsRequired();
		b.Property(e => e.ScheduleType).HasColumnName("schedule_type").HasMaxLength(40).IsRequired();
		b.Property(e => e.CronExpr).HasColumnName("cron_expr");
		b.Property(e => e.Agent).HasColumnName("agent");
		b.Property(e => e.TaskId).HasColumnName("task_id");
		b.Property(e => e.TaskPriority).HasColumnName("task_priority");
		b.Property(e => e.TaskType).HasColumnName("task_type");
		b.Property(e => e.EpicId).HasColumnName("epic_id");
		b.Property(e => e.Enabled).HasColumnName("enabled").HasDefaultValue(true);
		b.Property(e => e.NextRunAt).HasColumnName("next_run_at");
		b.Property(e => e.LastRunAt).HasColumnName("last_run_at");
		b.Property(e => e.CreatedAt).HasColumnName("created_at");
		b.Property(e => e.UpdatedAt).HasColumnName("updated_at");
		b.Property(e => e.CreatedBy).HasColumnName("created_by");
		b.Property(e => e.UpdatedBy).HasColumnName("updated_by");
		b.HasIndex(e => e.ProjectId);
	}
}

public sealed class AgentRunConfiguration : ReadOnlyConfiguration<AgentRun>
{
	protected override void ConfigureEntity(EntityTypeBuilder<AgentRun> b)
	{
		b.ToTable("agent_runs");
		b.Property(e => e.ScheduleId).HasColumnName("schedule_id").IsRequired();
		b.Property(e => e.TaskId).HasColumnName("task_id");
		b.Property(e => e.AgentRegistryId).HasColumnName("agent_registry_id");
		b.Property(e => e.AssignmentId).HasColumnName("assignment_id");
		b.Property(e => e.Agent).HasColumnName("agent").HasMaxLength(64);
		b.Property(e => e.Model).HasColumnName("model").HasMaxLength(100);
		b.Property(e => e.Status).HasColumnName("status").HasMaxLength(20).HasDefaultValue("pending");
		b.Property(e => e.IdempotencyKey).HasColumnName("idempotency_key").HasMaxLength(128);
		b.Property(e => e.StartedAt).HasColumnName("started_at");
		b.Property(e => e.FinishedAt).HasColumnName("finished_at");
		b.Property(e => e.Output).HasColumnName("output");
		b.Property(e => e.ErrorMessage).HasColumnName("error_message");
		b.Property(e => e.Summary).HasColumnName("summary");
		b.Property(e => e.LogRef).HasColumnName("log_ref").HasMaxLength(512);
		b.Property(e => e.CreatedAt).HasColumnName("created_at");
		b.HasIndex(e => e.ScheduleId);
		b.HasIndex(e => e.TaskId);
		b.HasIndex(e => e.AgentRegistryId);
		b.HasIndex(e => e.AssignmentId);
		b.HasIndex(e => e.IdempotencyKey).IsUnique();
	}
}

public sealed class EpicConfiguration : ReadOnlyConfiguration<Epic>
{
	protected override void ConfigureEntity(EntityTypeBuilder<Epic> b)
	{
		b.ToTable("epics");
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
		b.ToTable("stories");
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
		b.ToTable("tasks");
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
		b.ToTable("comments");
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
		b.ToTable("project_members");
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
		b.ToTable("notifications");
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

// ===== New entity configurations (Phase 0补全) =====

public sealed class SprintConfiguration : ReadOnlyConfiguration<Sprint>
{
	protected override void ConfigureEntity(EntityTypeBuilder<Sprint> b)
	{
		b.ToTable("sprints");
		b.Property(e => e.ProjectId).HasColumnName("project_id").IsRequired();
		b.Property(e => e.Title).HasColumnName("title").HasMaxLength(300).IsRequired();
		b.Property(e => e.Goal).HasColumnName("goal");
		b.Property(e => e.Status).HasColumnName("status").HasMaxLength(20).HasDefaultValue("planning");
		b.Property(e => e.StartDate).HasColumnName("start_date");
		b.Property(e => e.EndDate).HasColumnName("end_date");
		b.Property(e => e.CreatedAt).HasColumnName("created_at");
		b.HasIndex(e => e.ProjectId);
	}
}

public sealed class AttachmentConfiguration : ReadOnlyConfiguration<Attachment>
{
	protected override void ConfigureEntity(EntityTypeBuilder<Attachment> b)
	{
		b.ToTable("attachments");
		b.Property(e => e.TaskId).HasColumnName("task_id").IsRequired();
		b.Property(e => e.Filename).HasColumnName("filename").HasMaxLength(255).IsRequired();
		b.Property(e => e.OriginalName).HasColumnName("original_name").HasMaxLength(500).IsRequired();
		b.Property(e => e.Size).HasColumnName("size").IsRequired();
		b.Property(e => e.MimeType).HasColumnName("mime_type").HasMaxLength(200).IsRequired();
		b.Property(e => e.CreatedAt).HasColumnName("created_at");
		b.HasIndex(e => e.TaskId);
	}
}

public sealed class AuditLogConfiguration : ReadOnlyConfiguration<AuditLog>
{
	protected override void ConfigureEntity(EntityTypeBuilder<AuditLog> b)
	{
		b.ToTable("audit_logs");
		b.Property(e => e.UserId).HasColumnName("user_id");
		b.Property(e => e.Action).HasColumnName("action").HasMaxLength(50).IsRequired();
		b.Property(e => e.EntityType).HasColumnName("entity_type").HasMaxLength(30).IsRequired();
		b.Property(e => e.EntityId).HasColumnName("entity_id");
		b.Property(e => e.Method).HasColumnName("method").HasMaxLength(10).IsRequired();
		b.Property(e => e.Path).HasColumnName("path").HasMaxLength(500).IsRequired();
		b.Property(e => e.IpAddress).HasColumnName("ip_address").HasMaxLength(45);
		b.Property(e => e.UserAgent).HasColumnName("user_agent").HasMaxLength(500);
		b.Property(e => e.RequestBody).HasColumnName("request_body");
		b.Property(e => e.ResponseStatus).HasColumnName("response_status");
		b.Property(e => e.DurationMs).HasColumnName("duration_ms");
		b.Property(e => e.CreatedAt).HasColumnName("created_at");
		b.HasIndex(e => e.UserId);
		b.HasIndex(e => e.EntityId);
	}
}

public sealed class TaskDependencyConfiguration : ReadOnlyConfiguration<TaskDependency>
{
	protected override void ConfigureEntity(EntityTypeBuilder<TaskDependency> b)
	{
		b.ToTable("task_dependencies");
		b.Property(e => e.TaskId).HasColumnName("task_id").IsRequired();
		b.Property(e => e.DependsOnId).HasColumnName("depends_on_id").IsRequired();
		b.Property(e => e.DependencyType).HasColumnName("dependency_type").HasMaxLength(20).HasDefaultValue("blocks");
		b.Property(e => e.CreatedAt).HasColumnName("created_at");
		b.HasIndex(e => e.TaskId);
		b.HasIndex(e => e.DependsOnId);
	}
}

public sealed class WebhookConfigConfiguration : ReadOnlyConfiguration<WebhookConfig>
{
	protected override void ConfigureEntity(EntityTypeBuilder<WebhookConfig> b)
	{
		b.ToTable("webhook_configs");
		b.Property(e => e.ProjectId).HasColumnName("project_id");
		b.Property(e => e.Name).HasColumnName("name").HasMaxLength(100).IsRequired();
		b.Property(e => e.Url).HasColumnName("url").HasMaxLength(2000).IsRequired();
		b.Property(e => e.Secret).HasColumnName("secret").HasMaxLength(256);
		b.Property(e => e.Events).HasColumnName("events");
		b.Property(e => e.Enabled).HasColumnName("enabled").HasDefaultValue(true);
		b.Property(e => e.CreatedBy).HasColumnName("created_by");
		b.Property(e => e.CreatedAt).HasColumnName("created_at");
		b.Property(e => e.UpdatedAt).HasColumnName("updated_at");
		b.HasIndex(e => e.ProjectId);
	}
}

public sealed class ApiKeyConfiguration : ReadOnlyConfiguration<ApiKey>
{
	protected override void ConfigureEntity(EntityTypeBuilder<ApiKey> b)
	{
		b.ToTable("api_keys");
		b.Property(e => e.UserId).HasColumnName("user_id").IsRequired();
		b.Property(e => e.AgentRegistryId).HasColumnName("agent_registry_id");
		b.Property(e => e.Name).HasColumnName("name").HasMaxLength(100).IsRequired();
		b.Property(e => e.KeyPrefix).HasColumnName("key_prefix").HasMaxLength(20).IsRequired();
		b.Property(e => e.KeyHash).HasColumnName("key_hash").HasMaxLength(256).IsRequired();
		b.Property(e => e.Scopes).HasColumnName("scopes");
		b.Property(e => e.Enabled).HasColumnName("enabled").HasDefaultValue(true);
		b.Property(e => e.LastUsedAt).HasColumnName("last_used_at");
		b.Property(e => e.CreatedAt).HasColumnName("created_at");
		b.HasIndex(e => e.UserId);
	}
}

public sealed class DocumentConfiguration : ReadOnlyConfiguration<Document>
{
	protected override void ConfigureEntity(EntityTypeBuilder<Document> b)
	{
		b.ToTable("documents");
		b.Property(e => e.ProjectId).HasColumnName("project_id").IsRequired();
		b.Property(e => e.EpicId).HasColumnName("epic_id");
		b.Property(e => e.StoryId).HasColumnName("story_id");
		b.Property(e => e.FolderId).HasColumnName("folder_id");
		b.Property(e => e.Title).HasColumnName("title").HasMaxLength(300).IsRequired();
		b.Property(e => e.Content).HasColumnName("content");
		b.Property(e => e.Type).HasColumnName("type").HasMaxLength(20).HasDefaultValue("plan");
		b.Property(e => e.Status).HasColumnName("status").HasMaxLength(20).HasDefaultValue("draft");
		b.Property(e => e.AuthorId).HasColumnName("author_id");
		b.Property(e => e.CurrentRevisionId).HasColumnName("current_revision_id").HasDefaultValue(0);
		b.Property(e => e.CurrentRevisionNumber).HasColumnName("current_revision_number").HasDefaultValue(0);
		b.Property(e => e.CreatedAt).HasColumnName("created_at");
		b.Property(e => e.UpdatedAt).HasColumnName("updated_at");
		b.HasIndex(e => e.ProjectId);
		b.HasIndex(e => e.FolderId);
	}
}

public sealed class DocumentRevisionConfiguration : ReadOnlyConfiguration<DocumentRevision>
{
	protected override void ConfigureEntity(EntityTypeBuilder<DocumentRevision> b)
	{
		b.ToTable("document_revisions");
		b.Property(e => e.DocumentId).HasColumnName("document_id").IsRequired();
		b.Property(e => e.RevisionNumber).HasColumnName("revision_number").IsRequired();
		b.Property(e => e.AuthorId).HasColumnName("author_id");
		b.Property(e => e.Author).HasColumnName("author").HasMaxLength(100).IsRequired();
		b.Property(e => e.Content).HasColumnName("content").IsRequired();
		b.Property(e => e.ChangeNote).HasColumnName("change_note").HasMaxLength(500);
		b.Property(e => e.CreatedAt).HasColumnName("created_at");
		b.HasIndex(e => e.DocumentId);
	}
}

public sealed class DocumentFolderConfiguration : ReadOnlyConfiguration<DocumentFolder>
{
	protected override void ConfigureEntity(EntityTypeBuilder<DocumentFolder> b)
	{
		b.ToTable("document_folders");
		b.Property(e => e.ProjectId).HasColumnName("project_id").IsRequired();
		b.Property(e => e.ParentId).HasColumnName("parent_id");
		b.Property(e => e.Name).HasColumnName("name").HasMaxLength(300).IsRequired();
		b.Property(e => e.CreatedAt).HasColumnName("created_at");
		b.Property(e => e.UpdatedAt).HasColumnName("updated_at");
		b.HasIndex(e => e.ProjectId);
		b.HasIndex(e => e.ParentId);
	}
}

public sealed class DocumentCommentConfiguration : ReadOnlyConfiguration<DocumentComment>
{
	protected override void ConfigureEntity(EntityTypeBuilder<DocumentComment> b)
	{
		b.ToTable("document_comments");
		b.Property(e => e.DocumentId).HasColumnName("document_id").IsRequired();
		b.Property(e => e.AuthorId).HasColumnName("author_id");
		b.Property(e => e.Author).HasColumnName("author").HasMaxLength(100).IsRequired();
		b.Property(e => e.Content).HasColumnName("content").IsRequired();
		b.Property(e => e.CreatedAt).HasColumnName("created_at");
		b.Property(e => e.UpdatedAt).HasColumnName("updated_at");
		b.HasIndex(e => e.DocumentId);
	}
}

public sealed class StoryStatusHistoryConfiguration : ReadOnlyConfiguration<StoryStatusHistory>
{
	protected override void ConfigureEntity(EntityTypeBuilder<StoryStatusHistory> b)
	{
		b.ToTable("story_status_history");
		b.Property(e => e.StoryId).HasColumnName("story_id").IsRequired();
		b.Property(e => e.FromStatus).HasColumnName("from_status").HasMaxLength(40).IsRequired();
		b.Property(e => e.ToStatus).HasColumnName("to_status").HasMaxLength(40).IsRequired();
		b.Property(e => e.ChangedBy).HasColumnName("changed_by");
		b.Property(e => e.Reason).HasColumnName("reason").HasMaxLength(200);
		b.Property(e => e.CreatedAt).HasColumnName("created_at");
		b.HasIndex(e => e.StoryId);
	}
}

public sealed class TaskStatusHistoryConfiguration : ReadOnlyConfiguration<TaskStatusHistory>
{
	protected override void ConfigureEntity(EntityTypeBuilder<TaskStatusHistory> b)
	{
		b.ToTable("task_status_history");
		b.Property(e => e.TaskId).HasColumnName("task_id").IsRequired();
		b.Property(e => e.FromStatus).HasColumnName("from_status").HasMaxLength(40).IsRequired();
		b.Property(e => e.ToStatus).HasColumnName("to_status").HasMaxLength(40).IsRequired();
		b.Property(e => e.ChangedBy).HasColumnName("changed_by");
		b.Property(e => e.Reason).HasColumnName("reason").HasMaxLength(200);
		b.Property(e => e.CreatedAt).HasColumnName("created_at");
		b.HasIndex(e => e.TaskId);
	}
}
