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

	/// <summary>
	/// P0-3: replace the previous <c>ListAsync(predicate)</c> scan with a
	/// single index seek against the unique <c>key_hash</c> column. The
	/// returned row is already filtered to <c>Enabled = true</c> at the
	/// call site (the previous implementation did this in memory).
	/// </summary>
	public Task<ApiKey?> GetByHashAsync(string keyHash, CancellationToken ct = default) =>
		Set.AsNoTracking().FirstOrDefaultAsync(k => k.KeyHash == keyHash, ct);
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
		bool? includeArchived = null,
		CancellationToken ct = default)
	{
		var ids = projectIds.ToArray();
		var query = _db.Projects.AsNoTracking().Where(p => ids.Contains(p.Id));
		if (!includePrivate)
			query = query.Where(p => !p.IsPrivate);
		query = scope switch
		{
			"archived" => query.Where(p => p.IsArchived),
			"all" when includeArchived is false => query.Where(p => !p.IsArchived),
			"all" => query,
			_ => query.Where(p => !p.IsArchived),
		};

		var rows = await query.ToListAsync(ct);
		var rowIds = rows.Select(p => p.Id).ToArray();
		var taskStats = await _db.Tasks.AsNoTracking()
			.Where(t => rowIds.Contains(t.ProjectId))
			.GroupBy(t => t.ProjectId)
			.Select(g => new
			{
				ProjectId = g.Key,
				TaskCount = g.Count(),
				TaskDone = g.Count(t => t.Status == "done"),
				LastActivityAt = g.Max(t => (DateTime?)t.UpdatedAt),
			})
			.ToDictionaryAsync(x => x.ProjectId, ct);
		var memberCounts = await _db.ProjectMembers.AsNoTracking()
			.Where(m => rowIds.Contains(m.ProjectId))
			.GroupBy(m => m.ProjectId)
			.Select(g => new { ProjectId = g.Key, Count = g.Count() })
			.ToDictionaryAsync(x => x.ProjectId, x => x.Count, ct);
		var epicActivity = await _db.Epics.AsNoTracking()
			.Where(e => rowIds.Contains(e.ProjectId))
			.GroupBy(e => e.ProjectId)
			.Select(g => new { ProjectId = g.Key, LastActivityAt = g.Max(e => (DateTime?)e.CreatedAt) })
			.ToDictionaryAsync(x => x.ProjectId, x => x.LastActivityAt, ct);
		var storyActivity = await (
			from story in _db.Stories.AsNoTracking()
			join epic in _db.Epics.AsNoTracking() on story.EpicId equals epic.Id
			where rowIds.Contains(epic.ProjectId)
			group story by epic.ProjectId into grouped
			select new { ProjectId = grouped.Key, LastActivityAt = grouped.Max(s => (DateTime?)s.CreatedAt) })
			.ToDictionaryAsync(x => x.ProjectId, x => x.LastActivityAt, ct);

		var items = rows.Select(project =>
		{
			taskStats.TryGetValue(project.Id, out var taskStat);
			epicActivity.TryGetValue(project.Id, out var epicAt);
			storyActivity.TryGetValue(project.Id, out var storyAt);
			var candidates = new[] { taskStat?.LastActivityAt, epicAt, storyAt };
			return new ProjectCenterItem(
				project.Id, project.Name, project.Key, project.Description, project.IsPrivate,
				project.CreatedAt, project.IsArchived, taskStat?.TaskCount ?? 0,
				taskStat?.TaskDone ?? 0, memberCounts.GetValueOrDefault(project.Id),
				candidates.Max());
		}).ToList();

		items = sort switch
		{
			"name" => items.OrderBy(i => i.Name).ThenBy(i => i.Id).ToList(),
			"created" => items.OrderByDescending(i => i.CreatedAt).ThenByDescending(i => i.Id).ToList(),
			"tasks" => items.OrderByDescending(i => i.TaskCount).ThenByDescending(i => i.CreatedAt).ThenByDescending(i => i.Id).ToList(),
			_ => items.OrderByDescending(i => i.LastActivityAt ?? i.CreatedAt).ThenByDescending(i => i.Id).ToList(),
		};

		return new ProjectsCenterResult(
			items.Skip(Math.Max(0, offset)).Take(Math.Clamp(limit, 1, 200)).ToList(),
			items.Count);
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
