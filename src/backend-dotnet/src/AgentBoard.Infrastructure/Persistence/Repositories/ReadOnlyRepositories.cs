// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board.Dtos;
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

public sealed class AgentScheduleRepository : Repository<AgentSchedule>, IAgentScheduleRepository
{
	public AgentScheduleRepository(AppDbContext db) : base(db) { }
	protected override DbSet<AgentSchedule> Set => Db.AgentSchedules;
}

public sealed class AgentRunRepository : Repository<AgentRun>, IAgentRunRepository
{
	public AgentRunRepository(AppDbContext db) : base(db) { }
	protected override DbSet<AgentRun> Set => Db.AgentRuns;
}

public sealed class ProjectReadRepository : IProjectReadRepository
{
	private readonly AppDbContext _db;

	public ProjectReadRepository(AppDbContext db) => _db = db ?? throw new ArgumentNullException(nameof(db));

	public async Task<ProjectMembersResult> ListMembersAsync(
		int projectId,
		int limit,
		int offset,
		CancellationToken ct = default)
	{
		var query =
			from member in _db.ProjectMembers.AsNoTracking()
			join user in _db.Users.AsNoTracking() on member.UserId equals user.Id into users
			from user in users.DefaultIfEmpty()
			where member.ProjectId == projectId
			orderby member.JoinedAt descending, member.Id descending
			select new ProjectMemberDto(
				member.Id,
				member.ProjectId,
				member.UserId,
				member.Role,
				member.JoinedAt,
				user == null ? null : user.Username);

		var total = await query.CountAsync(ct);
		var items = await query.Skip(offset).Take(limit).ToListAsync(ct);
		return new ProjectMembersResult(items, total);
	}

	public async Task<NotificationsResult> ListNotificationsAsync(
		int userId,
		int limit,
		int offset,
		bool unreadOnly,
		CancellationToken ct = default)
	{
		var query = _db.Notifications
			.AsNoTracking()
			.Where(notification => notification.UserId == userId);
		if (unreadOnly)
			query = query.Where(notification => !notification.IsRead);

		var total = await query.CountAsync(ct);
		var items = await query
			.OrderByDescending(notification => notification.CreatedAt)
			.ThenByDescending(notification => notification.Id)
			.Skip(offset)
			.Take(limit)
			.Select(notification => new NotificationDto(
				notification.Id,
				notification.UserId,
				notification.Type,
				notification.Title,
				notification.Content,
				notification.IsRead,
				notification.Link,
				notification.CreatedAt))
			.ToListAsync(ct);

		return new NotificationsResult(items, total);
	}

	public Task<int> CountUnreadNotificationsAsync(int userId, CancellationToken ct = default) =>
		_db.Notifications
			.AsNoTracking()
			.CountAsync(notification => notification.UserId == userId && !notification.IsRead, ct);

	public async Task<OverviewDto> GetOverviewAsync(
		IReadOnlyCollection<int> projectIds,
		CancellationToken ct = default)
	{
		var ids = projectIds.Distinct().ToArray();
		if (ids.Length == 0)
			return EmptyOverview();

		var projects = await _db.Projects
			.AsNoTracking()
			.Where(project => ids.Contains(project.Id))
			.Select(project => new { project.Id, project.Name })
			.ToListAsync(ct);
		var epicCount = await _db.Epics.AsNoTracking().CountAsync(epic => ids.Contains(epic.ProjectId), ct);
		var storyCount = await (
			from story in _db.Stories.AsNoTracking()
			join epic in _db.Epics.AsNoTracking() on story.EpicId equals epic.Id
			where ids.Contains(epic.ProjectId)
			select story.Id).CountAsync(ct);
		var taskQuery = _db.Tasks.AsNoTracking().Where(task => ids.Contains(task.ProjectId));
		var taskCount = await taskQuery.CountAsync(ct);
		var doneTaskCount = await taskQuery.CountAsync(task => task.Status == "done", ct);
		var taskGroups = await taskQuery
			.GroupBy(task => task.ProjectId)
			.Select(group => new
			{
				ProjectId = group.Key,
				Total = group.Count(),
				Done = group.Count(task => task.Status == "done"),
			})
			.ToListAsync(ct);
		var taskByProject = taskGroups.ToDictionary(group => group.ProjectId);

		var statusGroups = await taskQuery
			.GroupBy(task => task.Status)
			.Select(group => new { Status = group.Key, Count = group.Count() })
			.ToListAsync(ct);
		var statusByName = statusGroups.ToDictionary(group => group.Status, group => group.Count);
		var statuses = new[] { "todo", "in_progress", "in_review", "done", "blocked" };

		var since = DateTime.Now.Date.AddDays(-6);
		var activityGroups = await taskQuery
			.Where(task => task.UpdatedAt >= since)
			.GroupBy(task => task.UpdatedAt.Date)
			.Select(group => new { Day = group.Key, Count = group.Count() })
			.ToListAsync(ct);
		var activityByDay = activityGroups.ToDictionary(group => group.Day, group => group.Count);

		var progress = projects
			.Select(project =>
			{
				taskByProject.TryGetValue(project.Id, out var group);
				var total = group?.Total ?? 0;
				var done = group?.Done ?? 0;
				return new OverviewProjectProgress(
					project.Id,
					project.Name,
					total,
					done,
					total == 0 ? 0 : (int)Math.Round(done * 100.0 / total));
			})
			.OrderByDescending(item => item.Total)
			.ThenBy(item => item.Id)
			.ToList();

		var activity = Enumerable.Range(0, 7)
			.Select(index =>
			{
				var day = since.AddDays(index);
				return new DayCount(day.ToString("yyyy-MM-dd"), activityByDay.GetValueOrDefault(day, 0));
			})
			.ToList();

		return new OverviewDto(
			new OverviewCounts(projects.Count, epicCount, storyCount, taskCount, doneTaskCount),
			progress,
			statuses.Select(status => new StatusCount(status, statusByName.GetValueOrDefault(status, 0))).ToList(),
			activity);
	}

	public async Task<ProjectsCenterResult> GetCenterAsync(
		IReadOnlyCollection<int> projectIds,
		bool includePrivate,
		string scope,
		string sort,
		int limit,
		int offset,
		CancellationToken ct = default)
	{
		var ids = projectIds.ToArray();
		var query = _db.Projects.AsNoTracking().Where(p => ids.Contains(p.Id));
		if (!includePrivate)
			query = query.Where(p => !p.IsPrivate);
		query = scope switch
		{
			"archived" => query.Where(p => p.IsArchived),
			"all" => query,
			_ => query.Where(p => !p.IsArchived),
		};

		var projected = query.Select(p => new
		{
			p.Id,
			p.Name,
			p.Key,
			p.Description,
			p.IsPrivate,
			p.CreatedAt,
			p.IsArchived,
			TaskCount = _db.Tasks.Count(t => t.ProjectId == p.Id),
			TaskDone = _db.Tasks.Count(t => t.ProjectId == p.Id && t.Status == "done"),
		});

		var ordered = sort switch
		{
			"name" => projected.OrderBy(p => p.Name).ThenBy(p => p.Id),
			"tasks" => projected.OrderByDescending(p => p.TaskCount).ThenByDescending(p => p.CreatedAt),
			_ => projected.OrderByDescending(p => p.CreatedAt).ThenByDescending(p => p.Id),
		};

		var total = await ordered.CountAsync(ct);
		var rows = await ordered.Skip(Math.Max(0, offset)).Take(Math.Clamp(limit, 1, 200)).ToListAsync(ct);
		var page = rows.Select(p => new ProjectDto(
			p.Id, p.Name, p.Key, p.Description, p.IsPrivate, p.CreatedAt, p.IsArchived,
			p.TaskCount, p.TaskDone)).ToList();
		return new ProjectsCenterResult(page, page, total, scope, sort);
	}

	private static OverviewDto EmptyOverview() => new(
		new OverviewCounts(0, 0, 0, 0, 0),
		Array.Empty<OverviewProjectProgress>(),
		new[] { "todo", "in_progress", "in_review", "done", "blocked" }
			.Select(status => new StatusCount(status, 0))
			.ToList(),
		Enumerable.Range(0, 7)
			.Select(index => new DayCount(DateTime.Now.Date.AddDays(index - 6).ToString("yyyy-MM-dd"), 0))
			.ToList());
}
