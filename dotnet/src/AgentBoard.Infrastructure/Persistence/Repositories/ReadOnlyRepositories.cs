// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Domain.Entities;
using Microsoft.EntityFrameworkCore;

namespace AgentBoard.Infrastructure.Persistence.Repositories;

public sealed class ProjectRepository : Repository<Project>, IProjectRepository
{
    public ProjectRepository(AppDbContext db) : base(db) { }
    protected override DbSet<Project> Set => Db.Set<Project>();
}

public sealed class EpicRepository : Repository<Epic>, IEpicRepository
{
    public EpicRepository(AppDbContext db) : base(db) { }
    protected override DbSet<Epic> Set => Db.Set<Epic>();
}

public sealed class StoryRepository : Repository<Story>, IStoryRepository
{
    public StoryRepository(AppDbContext db) : base(db) { }
    protected override DbSet<Story> Set => Db.Set<Story>();
}

public sealed class TaskItemRepository : Repository<TaskItem>, ITaskItemRepository
{
    public TaskItemRepository(AppDbContext db) : base(db) { }
    protected override DbSet<TaskItem> Set => Db.Set<TaskItem>();
}

public sealed class CommentRepository : Repository<Comment>, ICommentRepository
{
    public CommentRepository(AppDbContext db) : base(db) { }
    protected override DbSet<Comment> Set => Db.Set<Comment>();
}

public sealed class ProjectMemberRepository : Repository<ProjectMember>, IProjectMemberRepository
{
    public ProjectMemberRepository(AppDbContext db) : base(db) { }
    protected override DbSet<ProjectMember> Set => Db.Set<ProjectMember>();
}

public sealed class NotificationRepository : Repository<Notification>, INotificationRepository
{
    public NotificationRepository(AppDbContext db) : base(db) { }
    protected override DbSet<Notification> Set => Db.Set<Notification>();
}

// ===== New entity repositories (Phase 0补全) =====

public sealed class SprintRepository : Repository<Sprint>, ISprintRepository
{
    public SprintRepository(AppDbContext db) : base(db) { }
    protected override DbSet<Sprint> Set => Db.Set<Sprint>();
}

public sealed class AttachmentRepository : Repository<Attachment>, IAttachmentRepository
{
    public AttachmentRepository(AppDbContext db) : base(db) { }
    protected override DbSet<Attachment> Set => Db.Set<Attachment>();
}

public sealed class AuditLogRepository : Repository<AuditLog>, IAuditLogRepository
{
    public AuditLogRepository(AppDbContext db) : base(db) { }
    protected override DbSet<AuditLog> Set => Db.Set<AuditLog>();
}

public sealed class TaskDependencyRepository : Repository<TaskDependency>, ITaskDependencyRepository
{
    public TaskDependencyRepository(AppDbContext db) : base(db) { }
    protected override DbSet<TaskDependency> Set => Db.Set<TaskDependency>();
}

public sealed class WebhookConfigRepository : Repository<WebhookConfig>, IWebhookConfigRepository
{
    public WebhookConfigRepository(AppDbContext db) : base(db) { }
    protected override DbSet<WebhookConfig> Set => Db.Set<WebhookConfig>();
}

public sealed class ApiKeyRepository : Repository<ApiKey>, IApiKeyRepository
{
    public ApiKeyRepository(AppDbContext db) : base(db) { }
    protected override DbSet<ApiKey> Set => Db.Set<ApiKey>();
}

public sealed class DocumentRepository : Repository<Document>, IDocumentRepository
{
    public DocumentRepository(AppDbContext db) : base(db) { }
    protected override DbSet<Document> Set => Db.Set<Document>();
}

public sealed class DocumentRevisionRepository : Repository<DocumentRevision>, IDocumentRevisionRepository
{
    public DocumentRevisionRepository(AppDbContext db) : base(db) { }
    protected override DbSet<DocumentRevision> Set => Db.Set<DocumentRevision>();
}

public sealed class DocumentFolderRepository : Repository<DocumentFolder>, IDocumentFolderRepository
{
    public DocumentFolderRepository(AppDbContext db) : base(db) { }
    protected override DbSet<DocumentFolder> Set => Db.Set<DocumentFolder>();
}

public sealed class DocumentCommentRepository : Repository<DocumentComment>, IDocumentCommentRepository
{
    public DocumentCommentRepository(AppDbContext db) : base(db) { }
    protected override DbSet<DocumentComment> Set => Db.Set<DocumentComment>();
}

public sealed class StoryStatusHistoryRepository : Repository<StoryStatusHistory>, IStoryStatusHistoryRepository
{
    public StoryStatusHistoryRepository(AppDbContext db) : base(db) { }
    protected override DbSet<StoryStatusHistory> Set => Db.Set<StoryStatusHistory>();
}

public sealed class TaskStatusHistoryRepository : Repository<TaskStatusHistory>, ITaskStatusHistoryRepository
{
    public TaskStatusHistoryRepository(AppDbContext db) : base(db) { }
    protected override DbSet<TaskStatusHistory> Set => Db.Set<TaskStatusHistory>();
}
