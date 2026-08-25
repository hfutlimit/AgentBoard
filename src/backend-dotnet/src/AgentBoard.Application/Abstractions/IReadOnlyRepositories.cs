// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Entities;
using AgentBoard.Application.Board.Dtos;

namespace AgentBoard.Application.Abstractions;

/// <summary>Repository contracts for all FastAPI-owned tables.</summary>
public interface IProjectRepository : IRepository<Project> { }
public interface IEpicRepository : IRepository<Epic> { }
public interface IStoryRepository : IRepository<Story> { }
public interface ITaskItemRepository : IRepository<TaskItem> { }
public interface ICommentRepository : IRepository<Comment> { }
public interface IProjectMemberRepository : IRepository<ProjectMember> { }
public interface INotificationRepository : IRepository<Notification> { }
public interface ISprintRepository : IRepository<Sprint> { }
public interface IAttachmentRepository : IRepository<Attachment> { }
public interface IAuditLogRepository : IRepository<AuditLog> { }
public interface ITaskDependencyRepository : IRepository<TaskDependency> { }
public interface IWebhookConfigRepository : IRepository<WebhookConfig> { }
public interface IApiKeyRepository : IRepository<ApiKey>
{
    /// <summary>
    /// Look up an enabled API key by its full SHA-256 hex digest. Returns
    /// null when no enabled key matches. Uses the unique <c>key_hash</c>
    /// index so this is a single-row index seek.
    /// </summary>
    Task<ApiKey?> GetByHashAsync(string keyHash, CancellationToken ct = default);
}
public interface IDocumentRepository : IRepository<Document> { }
public interface IDocumentRevisionRepository : IRepository<DocumentRevision> { }
public interface IDocumentFolderRepository : IRepository<DocumentFolder> { }
public interface IDocumentCommentRepository : IRepository<DocumentComment> { }
public interface IStoryStatusHistoryRepository : IRepository<StoryStatusHistory> { }
public interface ITaskStatusHistoryRepository : IRepository<TaskStatusHistory> { }

public interface IProjectReadRepository
{
	Task<ProjectMembersResult> ListMembersAsync(
		int projectId,
		int limit,
		int offset,
		CancellationToken ct = default);

	Task<NotificationsResult> ListNotificationsAsync(
		int userId,
		int limit,
		int offset,
		bool unreadOnly,
		CancellationToken ct = default);

	Task<int> CountUnreadNotificationsAsync(int userId, CancellationToken ct = default);

	Task<OverviewDto> GetOverviewAsync(
		IReadOnlyCollection<int> projectIds,
		CancellationToken ct = default);

	Task<ProjectsCenterResult> GetCenterAsync(
		IReadOnlyCollection<int> projectIds,
		bool includePrivate,
		string scope,
		string sort,
		int limit,
		int offset,
		bool? includeArchived = null,
		CancellationToken ct = default);
}
