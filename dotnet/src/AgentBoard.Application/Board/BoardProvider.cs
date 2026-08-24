// SPDX-License-Identifier: MIT
using System.Collections.Generic;
using System.Linq;
using System.Linq.Expressions;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board.Dtos;
using AgentBoard.Application.Events;
using AgentBoard.Application.Scheduling.Dtos;
using AgentBoard.Domain.Common;
using AgentBoard.Domain.Entities;
using AgentBoard.Domain.Identity;

namespace AgentBoard.Application.Board;

public sealed class BoardProvider : IBoardProvider
{
	private readonly IProjectRepository _projects;
	private readonly IEpicRepository _epics;
	private readonly IStoryRepository _stories;
	private readonly ITaskItemRepository _tasks;
	private readonly ICommentRepository _comments;
	private readonly IProjectMemberRepository _members;
	private readonly IUserRepository _users;
	private readonly INotificationRepository _notifications;
	private readonly ITaskDependencyRepository _dependencies;
	private readonly IStoryStatusHistoryRepository _storyHistory;
	private readonly ITaskStatusHistoryRepository _taskHistory;
	private readonly IAttachmentRepository _attachments;
	private readonly ISprintRepository _sprints;
	private readonly IAgentScheduleRepository _schedules;
	private readonly IProjectReadRepository _readQueries;
	private readonly IProjectLifecycleService _projectLifecycle;
	private readonly IApplicationEventPublisher _events;
	private readonly IUnitOfWork _uow;

	public BoardProvider(
		IProjectRepository projects,
		IEpicRepository epics,
		IStoryRepository stories,
		ITaskItemRepository tasks,
		ICommentRepository comments,
		IProjectMemberRepository members,
		IUserRepository users,
		INotificationRepository notifications,
		ITaskDependencyRepository dependencies,
		IStoryStatusHistoryRepository storyHistory,
		ITaskStatusHistoryRepository taskHistory,
		IAttachmentRepository attachments,
		ISprintRepository sprints,
		IAgentScheduleRepository schedules,
		IProjectReadRepository readQueries,
		IProjectLifecycleService projectLifecycle,
		IApplicationEventPublisher events,
		IUnitOfWork uow)
	{
		_projects = projects ?? throw new ArgumentNullException(nameof(projects));
		_epics = epics ?? throw new ArgumentNullException(nameof(epics));
		_stories = stories ?? throw new ArgumentNullException(nameof(stories));
		_tasks = tasks ?? throw new ArgumentNullException(nameof(tasks));
		_comments = comments ?? throw new ArgumentNullException(nameof(comments));
		_members = members ?? throw new ArgumentNullException(nameof(members));
		_users = users ?? throw new ArgumentNullException(nameof(users));
		_notifications = notifications ?? throw new ArgumentNullException(nameof(notifications));
		_dependencies = dependencies ?? throw new ArgumentNullException(nameof(dependencies));
		_storyHistory = storyHistory ?? throw new ArgumentNullException(nameof(storyHistory));
		_taskHistory = taskHistory ?? throw new ArgumentNullException(nameof(taskHistory));
		_attachments = attachments ?? throw new ArgumentNullException(nameof(attachments));
		_sprints = sprints ?? throw new ArgumentNullException(nameof(sprints));
		_schedules = schedules ?? throw new ArgumentNullException(nameof(schedules));
		_readQueries = readQueries ?? throw new ArgumentNullException(nameof(readQueries));
		_projectLifecycle = projectLifecycle ?? throw new ArgumentNullException(nameof(projectLifecycle));
		_events = events ?? throw new ArgumentNullException(nameof(events));
		_uow = uow ?? throw new ArgumentNullException(nameof(uow));
	}

	public async Task<IReadOnlyList<ProjectDto>> ListProjectsAsync(CancellationToken ct = default)
	{
		var items = await _projects.ListAsync(ct: ct);
		return items.Select(ToProjectDto).ToList();
	}

	public async Task<ProjectDto?> GetProjectAsync(int id, CancellationToken ct = default)
	{
		var p = await _projects.GetByIdAsync(id, ct);
		return p is null ? null : ToProjectDto(p);
	}

	public async Task<IReadOnlyList<EpicDto>> ListEpicsAsync(int? projectId, CancellationToken ct = default)
	{
		var items = await _epics.ListAsync(projectId is null ? null : e => e.ProjectId == projectId, ct);
		return items.Select(ToEpicDto).ToList();
	}

	public async Task<EpicDto?> GetEpicAsync(int id, CancellationToken ct = default)
	{
		var e = await _epics.GetByIdAsync(id, ct);
		return e is null ? null : ToEpicDto(e);
	}

	public async Task<IReadOnlyList<StoryDto>> ListStoriesAsync(int? epicId, CancellationToken ct = default)
	{
		var items = await _stories.ListAsync(epicId is null ? null : s => s.EpicId == epicId, ct);
		return items.Select(ToStoryDto).ToList();
	}

	public async Task<StoryDto?> GetStoryAsync(int id, CancellationToken ct = default)
	{
		var s = await _stories.GetByIdAsync(id, ct);
		return s is null ? null : ToStoryDto(s);
	}

	public async Task<IReadOnlyList<TaskItemDto>> ListTasksAsync(int? projectId, int? storyId, CancellationToken ct = default)
	{
		Expression<Func<TaskItem, bool>>? pred = null;
		if (projectId is not null) pred = t => t.ProjectId == projectId;
		else if (storyId is not null) pred = t => t.StoryId == storyId;
		var items = await _tasks.ListAsync(pred, ct);
		return items.Select(ToTaskDto).ToList();
	}

	public async Task<TaskItemDto?> GetTaskAsync(int id, CancellationToken ct = default)
	{
		var t = await _tasks.GetByIdAsync(id, ct);
		return t is null ? null : ToTaskDto(t);
	}

	public async Task<IReadOnlyList<CommentDto>> ListCommentsAsync(
		int? taskId, int? storyId, int? epicId, CancellationToken ct = default)
	{
		Expression<Func<Comment, bool>>? pred = null;
		if (taskId is not null) pred = c => c.TaskId == taskId;
		else if (storyId is not null) pred = c => c.StoryId == storyId;
		else if (epicId is not null) pred = c => c.EpicId == epicId;
		var items = await _comments.ListAsync(pred, ct);
		return items.Select(ToCommentDto).ToList();
	}

	public async Task<CommentDto?> GetCommentAsync(int id, CancellationToken ct = default)
	{
		var c = await _comments.GetByIdAsync(id, ct);
		return c is null ? null : ToCommentDto(c);
	}

	// ===================== P2: write operations =====================

	/// <inheritdoc cref="IBoardProvider.CreateCommentAsync"/>
	public async Task<CommentDto> CreateCommentAsync(
		int? taskId, int? storyId, int? epicId, string? author, string? content, CancellationToken ct = default)
	{
		// Exactly one of task/story/epic must be set 鈥?mirrors FastAPI _comment_target.
		int targetId;
		string targetName;
		if (taskId is not null)
		{
			targetId = taskId.Value; targetName = "task";
			if (storyId is not null || epicId is not null)
				throw new InvalidValueException("exactly one of task_id/story_id/epic_id must be set");
		}
		else if (storyId is not null)
		{
			targetId = storyId.Value; targetName = "story";
			if (epicId is not null)
				throw new InvalidValueException("exactly one of task_id/story_id/epic_id must be set");
		}
		else if (epicId is not null)
		{
			targetId = epicId.Value; targetName = "epic";
		}
		else
		{
			throw new InvalidValueException("exactly one of task_id/story_id/epic_id must be set");
		}

		Entity? target = targetName switch
		{
			"task" => await _tasks.GetByIdAsync(targetId, ct),
			"story" => await _stories.GetByIdAsync(targetId, ct),
			_ => await _epics.GetByIdAsync(targetId, ct),
		};
		if (target is null)
			throw new NotFoundException($"{targetName} {targetId} not found");

		author = (author ?? string.Empty).Trim();
		content = (content ?? string.Empty).Trim();
		if (author.Length == 0 || content.Length == 0)
			throw new InvalidValueException("author and content are required");

		var comment = new Comment
		{
			TaskId = taskId,
			StoryId = storyId,
			EpicId = epicId,
			Author = author.Length <= 100 ? author : author[..100],
			Content = content,
			CreatedAt = DateTime.UtcNow,
			UpdatedAt = DateTime.UtcNow,
		};
		await _comments.AddAsync(comment, ct);
		await _uow.SaveChangesAsync(ct);
		return ToCommentDto(comment);
	}

	/// <inheritdoc cref="IBoardProvider.DeleteCommentAsync"/>
	public async Task<bool> DeleteCommentAsync(int id, CancellationToken ct = default)
	{
		var comment = await _comments.GetByIdAsync(id, ct);
		if (comment is null) return false;
		_comments.Remove(comment);
		await _uow.SaveChangesAsync(ct);
		return true;
	}

	// ===================== P2: project writes =====================

	/// <inheritdoc cref="IBoardProvider.CreateProjectAsync"/>
	public async Task<ProjectDto> CreateProjectAsync(
		string? name, string? key, string? description, int? currentUserId, CancellationToken ct = default)
		=> await _projectLifecycle.CreateAsync(name, key, description, currentUserId, ct);

	/// <inheritdoc cref="IBoardProvider.UpdateProjectAsync"/>
	public async Task<ProjectDto?> UpdateProjectAsync(
		int id, string? name, string? key, string? description, bool? isPrivate, bool? isArchived, CancellationToken ct = default)
	{
		var p = await _projects.GetByIdAsync(id, ct);
		if (p is null) return null;

		if (name is not null)
		{
			name = name.Trim();
			if (name.Length == 0 || name.Length > 200)
				throw new InvalidValueException("name must be 1-200 characters");
			p.Name = name;
		}
		if (key is not null)
		{
			key = key.Trim();
			if (key.Length == 0) key = null;
			if (key is not null)
			{
				if (key.Length > 20)
					throw new InvalidValueException("key must be at most 20 characters");
				var clash = await _projects.ListAsync(x => x.Key == key && x.Id != id, ct);
				if (clash.Count != 0)
					throw new DuplicateException($"project key '{key}' already exists");
			}
			p.Key = key;
		}
		if (description is not null) p.Description = description;
		if (isPrivate is not null) p.IsPrivate = isPrivate.Value;
		if (isArchived is not null)
		{
			p.IsArchived = isArchived.Value;
			if (isArchived.Value) p.ArchivedAt = DateTime.UtcNow;
			else { p.ArchivedAt = null; p.ArchivedBy = null; }
		}

		_projects.Update(p);
		await _uow.SaveChangesAsync(ct);
		return ToProjectDto(p);
	}

	/// <inheritdoc cref="IBoardProvider.DeleteProjectAsync"/>
	public async Task<bool> DeleteProjectAsync(int id, CancellationToken ct = default)
		=> await _projectLifecycle.DeleteAsync(id, ct);

	// ===================== P1: dashboard / board reads =====================

	/// <summary>Cross-project overview. Admin sees all; member sees own; anon sees empty.</summary>
	public async Task<OverviewDto> GetOverviewAsync(int? currentUserId, bool isAdmin, CancellationToken ct = default)
	{
		if (currentUserId is null)
			return EmptyOverview();

		var projectIds = isAdmin
			? (await _projects.ListAsync(ct: ct)).Select(p => p.Id).ToList()
			: (await _members.ListAsync(m => m.UserId == currentUserId, ct)).Select(m => m.ProjectId).ToList();
		return await _readQueries.GetOverviewAsync(projectIds, ct);
	}

	private static OverviewDto EmptyOverview() =>
		new(new OverviewCounts(0, 0, 0, 0, 0),
			new List<OverviewProjectProgress>(),
			new List<StatusCount>(),
			new List<DayCount>());

	public async Task<ProjectStatsDto?> GetProjectStatsAsync(int projectId, CancellationToken ct = default)
	{
		if (await _projects.GetByIdAsync(projectId, ct) is null)
			return null;

		var tasks = await _tasks.ListAsync(t => t.ProjectId == projectId, ct);
		var total = tasks.Count;
		var done = tasks.Count(t => t.Status == "done");
		var backlog = tasks.Count(t => t.Status == "todo");
		var active = tasks.Count(t => t.Status is "in_progress" or "in_review");

		var thirtyDaysAgo = DateTime.Now.Date.AddDays(-30);
		var dailyCreated = tasks
			.Where(t => t.CreatedAt >= thirtyDaysAgo)
			.GroupBy(t => t.CreatedAt.Date)
			.OrderBy(g => g.Key)
			.Select(g => new DayCount(g.Key.ToString("yyyy-MM-dd"), g.Count()))
			.ToList();
		var dailyDone = tasks
			.Where(t => t.Status == "done" && t.UpdatedAt >= thirtyDaysAgo)
			.GroupBy(t => t.UpdatedAt.Date)
			.OrderBy(g => g.Key)
			.Select(g => new DayCount(g.Key.ToString("yyyy-MM-dd"), g.Count()))
			.ToList();

		return new ProjectStatsDto(total, done, backlog, active, dailyCreated, dailyDone);
	}

	public async Task<KanbanDto?> GetProjectKanbanAsync(int projectId, bool includeAll, CancellationToken ct = default)
	{
		if (await _projects.GetByIdAsync(projectId, ct) is null)
			return null;

		var epicIds = (await _epics.ListAsync(e => e.ProjectId == projectId, ct))
			.Select(e => e.Id).ToHashSet();
		var stories = epicIds.Count > 0
			? (await _stories.ListAsync(s => epicIds.Contains(s.EpicId), ct))
				.Where(s => includeAll || s.InKanban)
				.OrderByDescending(s => s.Id)
				.ToList()
			: new List<Story>();

		var storyIds = stories.Select(s => (int?)s.Id).ToHashSet();
		var tasks = storyIds.Count > 0
			? await _tasks.ListAsync(t => storyIds.Contains(t.StoryId), ct)
			: new List<TaskItem>();
		var byStory = tasks
			.GroupBy(t => t.StoryId ?? 0)
			.ToDictionary(g => g.Key, g => (IReadOnlyList<KanbanTaskDto>)g.Select(ToKanbanTaskDto).ToList());

		var columns = new Dictionary<string, IReadOnlyList<KanbanStoryDto>>();
		foreach (var st in stories)
		{
			var dto = ToKanbanStoryDto(st, byStory.GetValueOrDefault(st.Id, Array.Empty<KanbanTaskDto>()));
			if (!columns.TryGetValue(st.Status, out var list))
			{
				list = new List<KanbanStoryDto>();
				columns[st.Status] = list;
			}
			((List<KanbanStoryDto>)list).Add(dto);
		}

		var items = stories
			.Select(st => ToKanbanStoryDto(st, byStory.GetValueOrDefault(st.Id, Array.Empty<KanbanTaskDto>())))
			.ToList();

		return new KanbanDto(columns, items);
	}

	public async Task<ProjectMembersResult?> ListProjectMembersAsync(int projectId, int limit, int offset, CancellationToken ct = default)
	{
		if (await _projects.GetByIdAsync(projectId, ct) is null)
			return null;
		return await _readQueries.ListMembersAsync(projectId, limit, offset, ct);
	}

	public async Task<NotificationsResult> ListNotificationsAsync(
		int userId, int limit, int offset, bool unreadOnly, CancellationToken ct = default)
	{
		return await _readQueries.ListNotificationsAsync(userId, limit, offset, unreadOnly, ct);
	}

	public async Task<int> GetUnreadNotificationCountAsync(int userId, CancellationToken ct = default)
	{
		return await _readQueries.CountUnreadNotificationsAsync(userId, ct);
	}

	// ===================== P3: Epic writes =====================

	/// <inheritdoc cref="IBoardProvider.CreateEpicAsync"/>
	public async Task<EpicDto> CreateEpicAsync(int projectId, string? title, string? description, CancellationToken ct = default)
	{
		title = (title ?? string.Empty).Trim();
		if (title.Length == 0 || title.Length > 200)
			throw new InvalidValueException("title must be 1-200 characters");

		if (await _projects.GetByIdAsync(projectId, ct) is null)
			throw new NotFoundException($"project {projectId} not found");

		var epic = new Epic
		{
			ProjectId = projectId,
			Title = title,
			Description = description ?? string.Empty,
			Status = "backlog",
			CreatedAt = DateTime.UtcNow,
		};

		await _epics.AddAsync(epic, ct);
		await _uow.SaveChangesAsync(ct);
		return ToEpicDto(epic);
	}

	/// <inheritdoc cref="IBoardProvider.UpdateEpicAsync"/>
	public async Task<EpicDto?> UpdateEpicAsync(int id, string? title, string? description, string? status, CancellationToken ct = default)
	{
		var epic = await _epics.GetByIdAsync(id, ct);
		if (epic is null) return null;

		if (title is not null)
		{
			title = title.Trim();
			if (title.Length == 0 || title.Length > 200)
				throw new InvalidValueException("title must be 1-200 characters");
			epic.Title = title;
		}
		if (description is not null) epic.Description = description;
		if (status is not null) epic.Status = status;

		_epics.Update(epic);
		await _uow.SaveChangesAsync(ct);
		return ToEpicDto(epic);
	}

	/// <inheritdoc cref="IBoardProvider.DeleteEpicAsync"/>
	public async Task<bool> DeleteEpicAsync(int id, CancellationToken ct = default)
	{
		var epic = await _epics.GetByIdAsync(id, ct);
		if (epic is null) return false;

		// Cascade: delete child stories 鈫?their tasks 鈫?their comments
		var stories = await _stories.ListAsync(s => s.EpicId == id, ct);
		var storyIds = stories.Select(s => s.Id).ToHashSet();
		var tasks = storyIds.Count == 0
			? Array.Empty<TaskItem>()
			: await _tasks.ListAsync(t => t.StoryId != null && storyIds.Contains(t.StoryId.Value), ct);

		foreach (var t in tasks)
			_comments.RemoveRange(await _comments.ListAsync(c => c.TaskId == t.Id, ct));
		foreach (var s in stories)
			_comments.RemoveRange(await _comments.ListAsync(c => c.StoryId == s.Id, ct));
		_comments.RemoveRange(await _comments.ListAsync(c => c.EpicId == id, ct));

		_tasks.RemoveRange(tasks);
		_stories.RemoveRange(stories);
		_epics.Remove(epic);
		await _uow.SaveChangesAsync(ct);
		return true;
	}

	// ===================== P3: Story writes =====================

	/// <inheritdoc cref="IBoardProvider.CreateStoryAsync"/>
	public async Task<StoryDto> CreateStoryAsync(int epicId, string? title, string? description, bool? needsDesign, CancellationToken ct = default)
	{
		title = (title ?? string.Empty).Trim();
		if (title.Length == 0 || title.Length > 200)
			throw new InvalidValueException("title must be 1-200 characters");

		if (await _epics.GetByIdAsync(epicId, ct) is null)
			throw new NotFoundException($"epic {epicId} not found");

		var story = new Story
		{
			EpicId = epicId,
			Title = title,
			Description = description ?? string.Empty,
			NeedsDesign = needsDesign ?? true,
			Status = "backlog",
			InKanban = false,
			CreatedAt = DateTime.UtcNow,
		};

		await _stories.AddAsync(story, ct);
		await _uow.SaveChangesAsync(ct);
		return ToStoryDto(story);
	}

	/// <inheritdoc cref="IBoardProvider.UpdateStoryAsync"/>
	public async Task<StoryDto?> UpdateStoryAsync(int id, string? title, string? description, string? status, bool? needsDesign, bool? inKanban, CancellationToken ct = default)
	{
		var story = await _stories.GetByIdAsync(id, ct);
		if (story is null) return null;

		if (title is not null)
		{
			title = title.Trim();
			if (title.Length == 0 || title.Length > 200)
				throw new InvalidValueException("title must be 1-200 characters");
			story.Title = title;
		}
		if (description is not null) story.Description = description;
		if (needsDesign is not null) story.NeedsDesign = needsDesign.Value;
		if (inKanban is not null) story.InKanban = inKanban.Value;

		// Status transition via update: validate and record history
		if (status is not null && status != story.Status)
		{
			var fromStatus = story.Status;
			await RecordStoryStatusHistoryAsync(story.Id, fromStatus, status, null, null, ct);
			story.Status = status;
		}

		_stories.Update(story);
		await _uow.SaveChangesAsync(ct);
		return ToStoryDto(story);
	}

	/// <inheritdoc cref="IBoardProvider.DeleteStoryAsync"/>
	public async Task<bool> DeleteStoryAsync(int id, CancellationToken ct = default)
	{
		var story = await _stories.GetByIdAsync(id, ct);
		if (story is null) return false;

		// Cascade: delete child tasks 鈫?their comments
		var tasks = await _tasks.ListAsync(t => t.StoryId == id, ct);
		foreach (var t in tasks)
			_comments.RemoveRange(await _comments.ListAsync(c => c.TaskId == t.Id, ct));
		_comments.RemoveRange(await _comments.ListAsync(c => c.StoryId == id, ct));
		_tasks.RemoveRange(tasks);
		_storyHistory.RemoveRange(await _storyHistory.ListAsync(h => h.StoryId == id, ct));
		_stories.Remove(story);
		await _uow.SaveChangesAsync(ct);
		return true;
	}

	/// <inheritdoc cref="IBoardProvider.ConfirmStoryAsync"/>
	public async Task<StoryDto?> ConfirmStoryAsync(int id, CancellationToken ct = default)
	{
		var story = await _stories.GetByIdAsync(id, ct);
		if (story is null) return null;

		if (story.Status != "backlog")
			throw new InvalidValueException($"story must be in 'backlog' to confirm, current status is '{story.Status}'");

		await RecordStoryStatusHistoryAsync(id, "backlog", "confirmed", CurrentUserId, null, ct);
		story.Status = "confirmed";
		_stories.Update(story);
		await _uow.SaveChangesAsync(ct);
		return ToStoryDto(story);
	}

	/// <inheritdoc cref="IBoardProvider.CompleteStoryAsync"/>
	public async Task<StoryDto?> CompleteStoryAsync(int id, CancellationToken ct = default)
	{
		var story = await _stories.GetByIdAsync(id, ct);
		if (story is null) return null;

		var terminalStatuses = new[] { "completed", "done", "cancelled" };
		if (terminalStatuses.Contains(story.Status))
			throw new InvalidValueException($"story is already in terminal status '{story.Status}'");

		await RecordStoryStatusHistoryAsync(id, story.Status, "completed", CurrentUserId, null, ct);
		story.Status = "completed";
		_stories.Update(story);
		await _uow.SaveChangesAsync(ct);
		return ToStoryDto(story);
	}

	/// <inheritdoc cref="IBoardProvider.GetStoryStatusHistoryAsync"/>
	public async Task<IReadOnlyList<StoryStatusHistoryDto>> GetStoryStatusHistoryAsync(int id, CancellationToken ct = default)
	{
		var items = await _storyHistory.ListAsync(h => h.StoryId == id, ct);
		return items
			.OrderByDescending(h => h.CreatedAt)
			.Select(h => new StoryStatusHistoryDto(h.Id, h.FromStatus, h.ToStatus, h.ChangedBy, h.Reason, h.CreatedAt))
			.ToList();
	}

	// ===================== P3: Task writes =====================

	/// <inheritdoc cref="IBoardProvider.CreateTaskAsync"/>
	public async Task<TaskItemDto> CreateTaskAsync(int storyId, string? type, string? title, string? priority, string? description, string? spec, int? assigneeId, CancellationToken ct = default)
	{
		var story = await _stories.GetByIdAsync(storyId, ct);
		if (story is null)
			throw new NotFoundException($"story {storyId} not found");

		title = (title ?? string.Empty).Trim();
		if (title.Length == 0 || title.Length > 200)
			throw new InvalidValueException("title must be 1-200 characters");

		var task = new TaskItem
		{
			ProjectId = story.EpicId, // resolved via epic below
			StoryId = storyId,
			Type = type ?? "dev",
			Title = title,
			Status = "todo",
			Priority = priority ?? "medium",
			Description = description ?? string.Empty,
			Spec = spec ?? string.Empty,
			AssigneeId = assigneeId,
			CreatedAt = DateTime.UtcNow,
			UpdatedAt = DateTime.UtcNow,
		};

		// Resolve ProjectId from story 鈫?epic
		var epic = await _epics.GetByIdAsync(story.EpicId, ct);
		if (epic is not null)
			task.ProjectId = epic.ProjectId;

		await _tasks.AddAsync(task, ct);
		await _uow.SaveChangesAsync(ct);
		return ToTaskDto(task);
	}

	/// <inheritdoc cref="IBoardProvider.UpdateTaskAsync"/>
	public async Task<TaskItemDto?> UpdateTaskAsync(int id, string? type, string? title, string? status, string? priority, string? statusReason, string? description, string? spec, int? assigneeId, string? dueDate, string? labels, double? estimate, int? complexity, string? neededCapabilities, string? domainTags, int? sprintId, int? reviewerId, CancellationToken ct = default)
	{
		var task = await _tasks.GetByIdAsync(id, ct);
		if (task is null) return null;

		if (type is not null) task.Type = type;
		if (title is not null)
		{
			title = title.Trim();
			if (title.Length == 0 || title.Length > 200)
				throw new InvalidValueException("title must be 1-200 characters");
			task.Title = title;
		}
		if (priority is not null) task.Priority = priority;
		if (statusReason is not null) task.StatusReason = statusReason;
		if (description is not null) task.Description = description;
		if (spec is not null) task.Spec = spec;
		if (assigneeId is not null) task.AssigneeId = assigneeId;
		if (labels is not null) task.Labels = labels;
		if (estimate is not null) task.Estimate = estimate;
		if (complexity is not null) task.Complexity = complexity;
		if (neededCapabilities is not null) task.NeededCapabilities = neededCapabilities;
		if (domainTags is not null) task.DomainTags = domainTags;
		if (sprintId is not null) task.SprintId = sprintId;
		if (reviewerId is not null) task.ReviewerId = reviewerId;

		if (dueDate is not null)
		{
			if (DateTime.TryParse(dueDate, out var parsed))
				task.DueDate = parsed;
		}

		// Status transition: validate and record history
		if (status is not null && status != task.Status)
		{
			var fromStatus = task.Status;
			await RecordTaskStatusHistoryAsync(id, fromStatus, status, null, null, ct);
			task.PreviousStatus = fromStatus;
			task.Status = status;
		}

		task.UpdatedAt = DateTime.UtcNow;
		_tasks.Update(task);
		await _uow.SaveChangesAsync(ct);
		await _events.PublishAsync(new TaskUpdatedEvent(task.Id, task.ProjectId, task.Status, DateTime.UtcNow), ct);
		return ToTaskDto(task);
	}

	/// <inheritdoc cref="IBoardProvider.DeleteTaskAsync"/>
	public async Task<bool> DeleteTaskAsync(int id, CancellationToken ct = default)
	{
		var task = await _tasks.GetByIdAsync(id, ct);
		if (task is null) return false;

		_comments.RemoveRange(await _comments.ListAsync(c => c.TaskId == id, ct));
		_dependencies.RemoveRange(await _dependencies.ListAsync(d => d.TaskId == id || d.DependsOnId == id, ct));
		_taskHistory.RemoveRange(await _taskHistory.ListAsync(h => h.TaskId == id, ct));
		_attachments.RemoveRange(await _attachments.ListAsync(a => a.TaskId == id, ct));
		_tasks.Remove(task);
		await _uow.SaveChangesAsync(ct);
		return true;
	}

	/// <inheritdoc cref="IBoardProvider.UpdateTaskStatusAsync"/>
	public async Task<TaskItemDto?> UpdateTaskStatusAsync(int id, string? status, string? statusReason, CancellationToken ct = default)
	{
		var task = await _tasks.GetByIdAsync(id, ct);
		if (task is null) return null;

		status = (status ?? string.Empty).Trim();
		if (status.Length == 0)
			throw new InvalidValueException("status is required");

		var fromStatus = task.Status;
		if (status == fromStatus)
			return ToTaskDto(task); // no-op

		await RecordTaskStatusHistoryAsync(id, fromStatus, status, CurrentUserId, statusReason, ct);
		task.PreviousStatus = fromStatus;
		task.Status = status;
		task.StatusReason = statusReason;
		task.UpdatedAt = DateTime.UtcNow;

		_tasks.Update(task);
		await _uow.SaveChangesAsync(ct);
		await _events.PublishAsync(new TaskUpdatedEvent(task.Id, task.ProjectId, task.Status, DateTime.UtcNow), ct);
		return ToTaskDto(task);
	}

	/// <inheritdoc cref="IBoardProvider.BulkUpdateTasksAsync"/>
	public async Task<IReadOnlyList<TaskItemDto>> BulkUpdateTasksAsync(List<int>? taskIds, string? status, string? priority, int? assigneeId, string? dueDate, CancellationToken ct = default)
	{
		if (taskIds is null || taskIds.Count == 0)
			throw new InvalidValueException("task_ids is required and must not be empty");

		var results = new List<TaskItemDto>();
		foreach (var id in taskIds)
		{
			var task = await _tasks.GetByIdAsync(id, ct);
			if (task is null) continue;

			if (status is not null && status != task.Status)
			{
				var fromStatus = task.Status;
				await RecordTaskStatusHistoryAsync(id, fromStatus, status, CurrentUserId, null, ct);
				task.PreviousStatus = fromStatus;
				task.Status = status;
			}
			if (priority is not null) task.Priority = priority;
			if (assigneeId is not null) task.AssigneeId = assigneeId;
			if (dueDate is not null && DateTime.TryParse(dueDate, out var parsed))
				task.DueDate = parsed;

			task.UpdatedAt = DateTime.UtcNow;
			_tasks.Update(task);
			results.Add(ToTaskDto(task));
		}

		await _uow.SaveChangesAsync(ct);
		return results;
	}

	/// <inheritdoc cref="IBoardProvider.BulkDeleteTasksAsync"/>
	public async Task<int> BulkDeleteTasksAsync(List<int>? taskIds, CancellationToken ct = default)
	{
		if (taskIds is null || taskIds.Count == 0)
			throw new InvalidValueException("task_ids is required and must not be empty");

		var count = 0;
		foreach (var id in taskIds)
		{
			var task = await _tasks.GetByIdAsync(id, ct);
			if (task is null) continue;

			_comments.RemoveRange(await _comments.ListAsync(c => c.TaskId == id, ct));
			_dependencies.RemoveRange(await _dependencies.ListAsync(d => d.TaskId == id || d.DependsOnId == id, ct));
			_taskHistory.RemoveRange(await _taskHistory.ListAsync(h => h.TaskId == id, ct));
			_attachments.RemoveRange(await _attachments.ListAsync(a => a.TaskId == id, ct));
			_tasks.Remove(task);
			count++;
		}

		await _uow.SaveChangesAsync(ct);
		return count;
	}

	// ===================== P3: Task dependencies =====================

	/// <inheritdoc cref="IBoardProvider.GetTaskDependenciesAsync"/>
	public async Task<IReadOnlyList<TaskDependencyDto>> GetTaskDependenciesAsync(int taskId, CancellationToken ct = default)
	{
		var items = await _dependencies.ListAsync(d => d.TaskId == taskId, ct);
		return items
			.Select(d => new TaskDependencyDto(d.Id, d.TaskId, d.DependsOnId, d.DependencyType, d.CreatedAt))
			.ToList();
	}

	/// <inheritdoc cref="IBoardProvider.AddTaskDependencyAsync"/>
	public async Task<TaskDependencyDto> AddTaskDependencyAsync(int taskId, int? dependsOnId, string? dependencyType, CancellationToken ct = default)
	{
		if (await _tasks.GetByIdAsync(taskId, ct) is null)
			throw new NotFoundException($"task {taskId} not found");

		if (dependsOnId is null)
			throw new InvalidValueException("depends_on_id is required");

		if (await _tasks.GetByIdAsync(dependsOnId.Value, ct) is null)
			throw new NotFoundException($"dependency target task {dependsOnId.Value} not found");

		if (taskId == dependsOnId.Value)
			throw new InvalidValueException("a task cannot depend on itself");

		var dep = new TaskDependency
		{
			TaskId = taskId,
			DependsOnId = dependsOnId.Value,
			DependencyType = dependencyType ?? "blocks",
			CreatedAt = DateTime.UtcNow,
		};

		await _dependencies.AddAsync(dep, ct);
		await _uow.SaveChangesAsync(ct);
		return new TaskDependencyDto(dep.Id, dep.TaskId, dep.DependsOnId, dep.DependencyType, dep.CreatedAt);
	}

	/// <inheritdoc cref="IBoardProvider.RemoveTaskDependencyAsync"/>
	public async Task<bool> RemoveTaskDependencyAsync(int dependencyId, CancellationToken ct = default)
	{
		var dep = await _dependencies.GetByIdAsync(dependencyId, ct);
		if (dep is null) return false;
		_dependencies.Remove(dep);
		await _uow.SaveChangesAsync(ct);
		return true;
	}

	// ===================== P3: Attachments =====================

	/// <inheritdoc cref="IBoardProvider.ListAttachmentsAsync"/>
	public async Task<IReadOnlyList<AttachmentDto>> ListAttachmentsAsync(int taskId, CancellationToken ct = default)
	{
		var items = await _attachments.ListAsync(a => a.TaskId == taskId, ct);
		return items
			.OrderByDescending(a => a.CreatedAt)
			.Select(a => new AttachmentDto(a.Id, a.TaskId, a.Filename, a.OriginalName, a.Size, a.MimeType, a.CreatedAt))
			.ToList();
	}

	/// <inheritdoc cref="IBoardProvider.GetAttachmentInfoAsync"/>
	public async Task<AttachmentDto?> GetAttachmentInfoAsync(int attachmentId, CancellationToken ct = default)
	{
		var a = await _attachments.GetByIdAsync(attachmentId, ct);
		return a is null ? null : new AttachmentDto(a.Id, a.TaskId, a.Filename, a.OriginalName, a.Size, a.MimeType, a.CreatedAt);
	}

	/// <inheritdoc cref="IBoardProvider.DeleteAttachmentAsync"/>
	public async Task<bool> DeleteAttachmentAsync(int attachmentId, CancellationToken ct = default)
	{
		var a = await _attachments.GetByIdAsync(attachmentId, ct);
		if (a is null) return false;
		_attachments.Remove(a);
		await _uow.SaveChangesAsync(ct);
		return true;
	}

	// ===================== P3: Search =====================

	/// <inheritdoc cref="IBoardProvider.SearchTasksAsync"/>
	public async Task<IReadOnlyList<TaskItemDto>> SearchTasksAsync(string? q, int? projectId, int? storyId, string? status, string? priority, string? assigneeId, int limit, CancellationToken ct = default)
	{
		Expression<Func<TaskItem, bool>>? pred = null;
		if (projectId is not null) pred = t => t.ProjectId == projectId;
		else if (storyId is not null) pred = t => t.StoryId == storyId;

		var items = await _tasks.ListAsync(pred, ct);

		// Apply additional in-memory filters
		if (status is not null)
			items = items.Where(t => t.Status == status).ToList();
		if (priority is not null)
			items = items.Where(t => t.Priority == priority).ToList();
		if (assigneeId is not null && int.TryParse(assigneeId, out var assigneeIdVal))
			items = items.Where(t => t.AssigneeId == assigneeIdVal).ToList();

		// Text search (ilike-style: case-insensitive contains)
		if (!string.IsNullOrWhiteSpace(q))
		{
			q = q.Trim();
			items = items.Where(t =>
				(t.Title != null && t.Title.Contains(q, StringComparison.OrdinalIgnoreCase)) ||
				(t.Description != null && t.Description.Contains(q, StringComparison.OrdinalIgnoreCase))
			).ToList();
		}

		return items
			.OrderByDescending(t => t.Id)
			.Take(limit > 0 ? limit : 50)
			.Select(ToTaskDto)
			.ToList();
	}

	// ===================== P3: helpers =====================

	private int? CurrentUserId => null; // TODO: inject ICurrentUser when auth is wired

	private async Task RecordStoryStatusHistoryAsync(int storyId, string fromStatus, string toStatus, int? changedBy, string? reason, CancellationToken ct)
	{
		var history = new StoryStatusHistory
		{
			StoryId = storyId,
			FromStatus = fromStatus,
			ToStatus = toStatus,
			ChangedBy = changedBy,
			Reason = reason,
			CreatedAt = DateTime.UtcNow,
		};
		await _storyHistory.AddAsync(history, ct);
	}

	private async Task RecordTaskStatusHistoryAsync(int taskId, string fromStatus, string toStatus, int? changedBy, string? reason, CancellationToken ct)
	{
		var history = new TaskStatusHistory
		{
			TaskId = taskId,
			FromStatus = fromStatus,
			ToStatus = toStatus,
			ChangedBy = changedBy,
			Reason = reason,
			CreatedAt = DateTime.UtcNow,
		};
		await _taskHistory.AddAsync(history, ct);
	}

	// ===================== mappers =====================

	private static ProjectDto ToProjectDto(Project p) =>
		new(p.Id, p.Name, p.Key, p.Description, p.IsPrivate, p.CreatedAt, p.IsArchived);

	private static EpicDto ToEpicDto(Epic e) =>
		new(e.Id, e.ProjectId, e.Title, e.Description, e.Status, e.CreatedAt);

	private static StoryDto ToStoryDto(Story s) =>
		new(s.Id, s.EpicId, s.Title, s.Description, s.Status, s.NeedsDesign, s.ReviewerId, s.ReviewRound, s.InKanban, s.CreatedAt);

	private static TaskItemDto ToTaskDto(TaskItem t) =>
		new(t.Id, t.ProjectId, t.StoryId, t.Type, t.Title, t.Status, t.Priority, t.StatusReason,
			t.Description, t.AssigneeId, t.DueDate, t.Labels, t.Estimate, t.Complexity, t.CreatedAt, t.UpdatedAt);

	private static SprintDto ToSprintDto(Domain.Entities.Sprint s) =>
		new(s.Id, s.ProjectId, s.Title, s.Goal, s.Status, s.StartDate, s.EndDate, s.CreatedAt);

	private static AgentScheduleDto ToScheduleDto(AgentSchedule s) =>
		new(s.Id, s.ProjectId, s.Title, s.ScheduleType, s.CronExpr, s.Agent, s.TaskId,
			s.TaskPriority, s.TaskType, s.EpicId, s.Enabled, s.NextRunAt, s.LastRunAt,
			s.CreatedAt, s.UpdatedAt, s.CreatedBy, s.UpdatedBy);

	private static CommentDto ToCommentDto(Comment c) =>
		new(c.Id, c.TaskId, c.StoryId, c.EpicId, c.Author, c.Content, c.CreatedAt, c.UpdatedAt);

	private static KanbanTaskDto ToKanbanTaskDto(TaskItem t) =>
		new(t.Id, t.Type, t.Title, t.Status, t.Priority, t.AssigneeId, t.Estimate);

	private static KanbanStoryDto ToKanbanStoryDto(Story s, IReadOnlyList<KanbanTaskDto> tasks) =>
		new(s.Id, s.EpicId, s.Title, s.Description, s.Status, s.NeedsDesign, s.InKanban, tasks, s.CreatedAt);

	// ===================== P3: Project extensions =====================

	public async Task<IReadOnlyList<ProjectDto>> ListProjectsExtendedAsync(int limit, int offset, bool? includeArchived, int? currentUserId, CancellationToken ct = default)
	{
		var all = (await _projects.ListAsync(ct: ct)).AsEnumerable();
		if (includeArchived != true) all = all.Where(p => !p.IsArchived);
		if (currentUserId is not null)
		{
			var memberProjectIds = (await _members.ListAsync(m => m.UserId == currentUserId, ct)).Select(m => m.ProjectId).ToHashSet();
			all = all.Where(p => memberProjectIds.Contains(p.Id));
		}
		return all.OrderByDescending(p => p.Id).Skip(offset).Take(limit).Select(ToProjectDto).ToList();
	}

	public async Task<ProjectDto?> ArchiveProjectAsync(int id, CancellationToken ct = default)
	{
		var p = await _projects.GetByIdAsync(id, ct);
		if (p is null) return null;
		p.IsArchived = true; p.ArchivedAt = DateTime.UtcNow;
		_projects.Update(p); await _uow.SaveChangesAsync(ct);
		return ToProjectDto(p);
	}

	public async Task<ProjectDto?> UnarchiveProjectAsync(int id, CancellationToken ct = default)
	{
		var p = await _projects.GetByIdAsync(id, ct);
		if (p is null) return null;
		p.IsArchived = false; p.ArchivedAt = null; p.ArchivedBy = null;
		_projects.Update(p); await _uow.SaveChangesAsync(ct);
		return ToProjectDto(p);
	}

	public async Task<int> BulkArchiveProjectsAsync(List<int>? ids, CancellationToken ct = default)
	{
		if (ids is null || ids.Count == 0) return 0;
		int count = 0;
		foreach (var id in ids) { var p = await _projects.GetByIdAsync(id, ct); if (p is null) continue; p.IsArchived = true; p.ArchivedAt = DateTime.UtcNow; _projects.Update(p); count++; }
		if (count > 0) await _uow.SaveChangesAsync(ct);
		return count;
	}

	public async Task<int> BulkUnarchiveProjectsAsync(List<int>? ids, CancellationToken ct = default)
	{
		if (ids is null || ids.Count == 0) return 0;
		int count = 0;
		foreach (var id in ids) { var p = await _projects.GetByIdAsync(id, ct); if (p is null) continue; p.IsArchived = false; p.ArchivedAt = null; p.ArchivedBy = null; _projects.Update(p); count++; }
		if (count > 0) await _uow.SaveChangesAsync(ct);
		return count;
	}

	public async Task<TicketListResult> ListProjectTicketsAsync(int projectId, string statusFilter, string sort, string order, int limit, int offset, CancellationToken ct = default)
	{
		var epics = await _epics.ListAsync(e => e.ProjectId == projectId, ct);
		var epicIds = epics.Select(e => e.Id).ToHashSet();
		var stories = epicIds.Count > 0 ? await _stories.ListAsync(s => epicIds.Contains(s.EpicId), ct) : new List<Story>();
		var tasks = await _tasks.ListAsync(t => t.ProjectId == projectId, ct);
		var tickets = new List<TicketItem>();
		tickets.AddRange(epics.Select(e => new TicketItem("epic", e.Id, e.Title, e.Status, e.Description, e.CreatedAt, e.CreatedAt, null)));
		tickets.AddRange(stories.Select(s => new TicketItem("story", s.Id, s.Title, s.Status, s.Description, s.CreatedAt, s.CreatedAt, null)));
		tickets.AddRange(tasks.Select(t => new TicketItem("task", t.Id, t.Title, t.Status, t.Description, t.CreatedAt, t.UpdatedAt, null)));
		if (!string.IsNullOrWhiteSpace(statusFilter) && statusFilter != "all") tickets = tickets.Where(t => t.Status == statusFilter).ToList();
		var desc = string.Equals(order, "desc", StringComparison.OrdinalIgnoreCase);
		tickets = desc ? tickets.OrderByDescending(t => t.Id).ToList() : tickets.OrderBy(t => t.Id).ToList();
		return new TicketListResult(tickets.Skip(offset).Take(limit).ToList(), tickets.Count);
	}

	public async Task<IReadOnlyList<ProjectDto>> ListUserProjectsAsync(int userId, string? role, CancellationToken ct = default)
	{
		var memberPred = role is not null ? (Expression<Func<ProjectMember, bool>>)(m => m.UserId == userId && m.Role == role) : m => m.UserId == userId;
		var memberships = await _members.ListAsync(memberPred, ct);
		var projectIds = memberships.Select(m => m.ProjectId).ToHashSet();
		if (projectIds.Count == 0) return Array.Empty<ProjectDto>();
		var projects = await _projects.ListAsync(p => projectIds.Contains(p.Id), ct);
		return projects.Select(ToProjectDto).ToList();
	}

	// ===================== P3: Member management =====================

	public async Task<ProjectMemberDto> InviteMemberAsync(int projectId, int? userId, string? username, string? role, CancellationToken ct = default)
	{
		if (await _projects.GetByIdAsync(projectId, ct) is null) throw new NotFoundException($"project {projectId} not found");
		if (userId is null && !string.IsNullOrWhiteSpace(username))
		{
			var user = (await _users.ListAsync(u => u.Username == username, ct)).FirstOrDefault();
			if (user is null) throw new NotFoundException($"user '{username}' not found");
			userId = user.Id;
		}
		if (userId is null) throw new InvalidValueException("user_id or username is required");
		var existing = await _members.ListAsync(m => m.ProjectId == projectId && m.UserId == userId, ct);
		if (existing.Count > 0) throw new DuplicateException($"user {userId} is already a member");
		var member = new ProjectMember { ProjectId = projectId, UserId = userId.Value, Role = role ?? "member", JoinedAt = DateTime.UtcNow };
		await _members.AddAsync(member, ct); await _uow.SaveChangesAsync(ct);
		var u = await _users.GetByIdAsync(userId.Value, ct);
		return new ProjectMemberDto(member.Id, member.ProjectId, member.UserId, member.Role, member.JoinedAt, u?.Username);
	}

	public async Task<bool> RemoveMemberAsync(int projectId, int userId, CancellationToken ct = default)
	{
		var members = await _members.ListAsync(m => m.ProjectId == projectId && m.UserId == userId, ct);
		var member = members.FirstOrDefault();
		if (member is null) return false;
		_members.Remove(member); await _uow.SaveChangesAsync(ct);
		return true;
	}

	public async Task<ProjectMemberDto?> UpdateMemberRoleAsync(int projectId, int userId, string? role, CancellationToken ct = default)
	{
		var members = await _members.ListAsync(m => m.ProjectId == projectId && m.UserId == userId, ct);
		var member = members.FirstOrDefault();
		if (member is null) return null;
		if (role is not null) member.Role = role;
		_members.Update(member); await _uow.SaveChangesAsync(ct);
		var u = await _users.GetByIdAsync(userId, ct);
		return new ProjectMemberDto(member.Id, member.ProjectId, member.UserId, member.Role, member.JoinedAt, u?.Username);
	}

	// ===================== P3: Review stats =====================

	public async Task<ReviewStatsDto?> GetReviewStatsAsync(int projectId, int days, int? userId, CancellationToken ct = default)
	{
		if (await _projects.GetByIdAsync(projectId, ct) is null) return null;
		var epics = await _epics.ListAsync(e => e.ProjectId == projectId, ct);
		var epicIds = epics.Select(e => e.Id).ToHashSet();
		var stories = epicIds.Count > 0 ? await _stories.ListAsync(s => epicIds.Contains(s.EpicId), ct) : new List<Story>();
		var tasks = await _tasks.ListAsync(t => t.ProjectId == projectId, ct);
		var storyTotal = stories.Count; var storyDone = stories.Count(s => s.Status == "completed");
		var taskTotal = tasks.Count; var taskDone = tasks.Count(t => t.Status == "done");
		return new ReviewStatsDto("single", 1,
			new ReviewStatsAggregate(storyTotal, storyDone, 0, storyTotal - storyDone, 0),
			new ReviewStatsAggregate(taskTotal, taskDone, 0, taskTotal - taskDone, 0),
			0, 0, 0, new List<ReviewReviewerWorkload>(), new List<ReviewVoteProgress>());
	}

	// ===================== P6: workspace nested creation (BFF module 6, 2026-08-23) =====================

	public async Task<StoryDto?> CreateEpicStoryAsync(int epicId, string? title, string? description, CancellationToken ct = default)
	{
		if (await _epics.GetByIdAsync(epicId, ct) is null) return null;
		title = (title ?? string.Empty).Trim();
		if (title.Length == 0 || title.Length > 300)
			throw new InvalidValueException("title must be 1-300 characters");
		var story = new Story
		{
			EpicId = epicId,
			Title = title,
			Description = description ?? string.Empty,
			Status = "backlog",
			NeedsDesign = true,
			CreatedAt = DateTime.UtcNow,
		};
		await _stories.AddAsync(story, ct);
		await _uow.SaveChangesAsync(ct);
		return ToStoryDto(story);
	}

	public async Task<(IReadOnlyList<TaskItemDto> Items, int Total)> ListStoryTasksAsync(
		int storyId, string? status, int limit, int offset, CancellationToken ct = default)
	{
		var all = await _tasks.ListAsync(t => t.StoryId == storyId, ct);
		var filtered = string.IsNullOrWhiteSpace(status) ? all : all.Where(t => t.Status == status).ToList();
		var page = filtered.Skip(offset).Take(limit).ToList();
		return (page.Select(ToTaskDto).ToList(), filtered.Count);
	}

	public async Task<TaskItemDto?> CreateStoryTaskAsync(
		int storyId, string? type, string? title, string? priority,
		int? assigneeId, CancellationToken ct = default)
	{
		var story = await _stories.GetByIdAsync(storyId, ct);
		if (story is null) return null;
		var epic = await _epics.GetByIdAsync(story.EpicId, ct);
		if (epic is null) return null;
		title = (title ?? string.Empty).Trim();
		if (title.Length == 0 || title.Length > 300)
			throw new InvalidValueException("title must be 1-300 characters");
		var task = new TaskItem
		{
			StoryId = storyId,
			ProjectId = epic.ProjectId,
			Type = type ?? "dev",
			Title = title,
			Status = "todo",
			Priority = priority ?? "medium",
			Description = string.Empty,
			AssigneeId = assigneeId,
			CreatedAt = DateTime.UtcNow,
			UpdatedAt = DateTime.UtcNow,
		};
		await _tasks.AddAsync(task, ct);
		await _uow.SaveChangesAsync(ct);
		return ToTaskDto(task);
	}

	// ===================== P1: project center / workspace nested (BFF module 1, 2026-08-23) =====================

	public async Task<ProjectsCenterResult> ListProjectsCenterAsync(
		int? currentUserId, bool isAdmin, string scope, string sort,
		int limit, int offset, CancellationToken ct = default)
	{
		var projectIds = isAdmin || currentUserId is null
			? (await _projects.ListAsync(ct: ct)).Select(p => p.Id).ToList()
			: (await _members.ListAsync(m => m.UserId == currentUserId.Value, ct))
				.Select(m => m.ProjectId).Distinct().ToList();
		if (currentUserId is null && (scope == "mine" || scope == "created"))
			projectIds = new List<int>();
		return await _readQueries.GetCenterAsync(
			projectIds, currentUserId is not null || isAdmin, scope, sort, limit, offset, ct);
	}

	/// <summary>Helper: 拿 currentUserId 可见的 projects (是 member 的)。</summary>
	private async Task<List<Project>> GetMemberVisibleProjectsAsync(int currentUserId, CancellationToken ct)
	{
		var myMemberships = await _members.ListAsync(m => m.UserId == currentUserId, ct);
		if (myMemberships.Count == 0) return new List<Project>();
		var myProjectIds = myMemberships.Select(m => m.ProjectId).ToHashSet();
		return (await _projects.ListAsync(p => myProjectIds.Contains(p.Id), ct)).ToList();
	}

	public async Task<IReadOnlyList<EpicDto>> ListProjectEpicsAsync(
		int projectId, string? status, int limit, int offset, CancellationToken ct = default)
	{
		if (await _projects.GetByIdAsync(projectId, ct) is null) return new List<EpicDto>();
		var all = await _epics.ListAsync(e => e.ProjectId == projectId, ct);
		var filtered = string.IsNullOrWhiteSpace(status) ? all : all.Where(e => e.Status == status).ToList();
		var page = filtered.Skip(offset).Take(limit).ToList();
		return page.Select(ToEpicDto).ToList();
	}

	public async Task<EpicDto?> CreateProjectEpicAsync(int projectId, string? title, string? description, CancellationToken ct = default)
	{
		if (await _projects.GetByIdAsync(projectId, ct) is null) return null;
		title = (title ?? string.Empty).Trim();
		if (title.Length == 0 || title.Length > 300)
			throw new InvalidValueException("title must be 1-300 characters");
		var epic = new Epic
		{
			ProjectId = projectId,
			Title = title,
			Description = description ?? string.Empty,
			Status = "backlog",
			CreatedAt = DateTime.UtcNow,
		};
		await _epics.AddAsync(epic, ct);
		await _uow.SaveChangesAsync(ct);
		return ToEpicDto(epic);
	}

	public async Task<SprintDto?> CreateProjectSprintAsync(
		int projectId, string? title, string? goal, DateTime? startDate, DateTime? endDate, CancellationToken ct = default)
	{
		if (await _projects.GetByIdAsync(projectId, ct) is null) return null;
		title = (title ?? string.Empty).Trim();
		if (title.Length == 0 || title.Length > 300)
			throw new InvalidValueException("title must be 1-300 characters");
		var sprint = new Domain.Entities.Sprint
		{
			ProjectId = projectId,
			Title = title,
			Goal = goal ?? string.Empty,
			Status = "planning",
			StartDate = startDate,
			EndDate = endDate,
			CreatedAt = DateTime.UtcNow,
		};
		await _sprints.AddAsync(sprint, ct);
		await _uow.SaveChangesAsync(ct);
		return ToSprintDto(sprint);
	}

	public async Task<IReadOnlyList<AgentScheduleDto>> ListProjectSchedulesAsync(
		int projectId, int limit, int offset, CancellationToken ct = default)
	{
		if (await _projects.GetByIdAsync(projectId, ct) is null) return new List<AgentScheduleDto>();
		var schedules = await _schedules.ListAsync(s => s.ProjectId == projectId, ct);
		return schedules
			.OrderByDescending(s => s.CreatedAt)
			.Skip(Math.Max(0, offset))
			.Take(Math.Clamp(limit, 1, 200))
			.Select(ToScheduleDto)
			.ToList();
	}

	public async Task<AgentScheduleDto?> CreateProjectScheduleAsync(
		int projectId, string? title, string? scheduleType, string? cronExpr,
		int? currentUserId, CancellationToken ct = default)
	{
		if (await _projects.GetByIdAsync(projectId, ct) is null) return null;
		title = (title ?? string.Empty).Trim();
		scheduleType = (scheduleType ?? string.Empty).Trim();
		if (title.Length == 0 || title.Length > 300)
			throw new InvalidValueException("title must be 1-300 characters");
		if (scheduleType != "cron")
			throw new InvalidValueException("schedule_type must be cron");
		if (string.IsNullOrWhiteSpace(cronExpr))
			throw new InvalidValueException("cron_expr is required for cron schedules");

		var now = DateTime.UtcNow;
		var schedule = new AgentSchedule
		{
			ProjectId = projectId,
			Title = title,
			ScheduleType = scheduleType,
			CronExpr = cronExpr.Trim(),
			Enabled = true,
			CreatedAt = now,
			UpdatedAt = now,
			CreatedBy = currentUserId,
			UpdatedBy = currentUserId,
		};
		await _schedules.AddAsync(schedule, ct);
		await _uow.SaveChangesAsync(ct);
		return ToScheduleDto(schedule);
	}

	public async Task<ProjectExportDto?> ExportProjectAsync(int projectId, CancellationToken ct = default)
	{
		var project = await _projects.GetByIdAsync(projectId, ct);
		if (project is null) return null;
		var epics = await _epics.ListAsync(e => e.ProjectId == projectId, ct);
		var epicIds = epics.Select(e => e.Id).ToHashSet();
		var stories = epicIds.Count > 0
			? await _stories.ListAsync(s => epicIds.Contains(s.EpicId), ct)
			: new List<Domain.Entities.Story>();
		var tasks = await _tasks.ListAsync(t => t.ProjectId == projectId, ct);
		return new ProjectExportDto(
			ToProjectDto(project),
			epics.Select(ToEpicDto).ToList(),
			stories.Select(ToStoryDto).ToList(),
			tasks.Select(ToTaskDto).ToList(),
			DateTime.UtcNow);
	}

	public async Task<ProjectImportResult?> ImportProjectAsync(int targetProjectId, ProjectImportRequest body, CancellationToken ct = default)
	{
		if (await _projects.GetByIdAsync(targetProjectId, ct) is null) return null;
		// 简化实现: 把 epics/stories/tasks 都建到 target project 下, id 由 EF 重生
		var errors = new List<string>();
		var importedEpics = new List<int>();
		var importedStories = new List<int>();
		var importedTasks = new List<int>();
		foreach (var epicDto in body.Epics ?? new List<EpicDto>())
		{
			try
			{
				var newEpic = new Domain.Entities.Epic
				{
					ProjectId = targetProjectId,
					Title = epicDto.Title,
					Description = epicDto.Description,
					Status = epicDto.Status ?? "backlog",
					CreatedAt = DateTime.UtcNow,
				};
				await _epics.AddAsync(newEpic, ct);
				await _uow.SaveChangesAsync(ct);
				importedEpics.Add(newEpic.Id);
			}
			catch (Exception ex) { errors.Add($"epic '{epicDto.Title}': {ex.Message}"); }
		}
		foreach (var storyDto in body.Stories ?? new List<StoryDto>())
		{
			try
			{
				// 找一个 epic (从 importedEpics 取第一个, 简化)
				var epicId = importedEpics.FirstOrDefault();
				if (epicId == 0) { errors.Add($"story '{storyDto.Title}': no epic available"); continue; }
				var newStory = new Domain.Entities.Story
				{
					EpicId = epicId,
					Title = storyDto.Title,
					Description = storyDto.Description,
					Status = storyDto.Status ?? "backlog",
					NeedsDesign = storyDto.NeedsDesign,
					CreatedAt = DateTime.UtcNow,
				};
				await _stories.AddAsync(newStory, ct);
				await _uow.SaveChangesAsync(ct);
				importedStories.Add(newStory.Id);
			}
			catch (Exception ex) { errors.Add($"story '{storyDto.Title}': {ex.Message}"); }
		}
		foreach (var taskDto in body.Tasks ?? new List<TaskItemDto>())
		{
			try
			{
				var storyId = importedStories.FirstOrDefault();
				var newTask = new TaskItem
				{
					StoryId = storyId == 0 ? null : storyId,
					ProjectId = targetProjectId,
					Type = taskDto.Type ?? "dev",
					Title = taskDto.Title,
					Status = taskDto.Status ?? "todo",
					Priority = taskDto.Priority ?? "medium",
					Description = taskDto.Description,
					CreatedAt = DateTime.UtcNow,
					UpdatedAt = DateTime.UtcNow,
				};
				await _tasks.AddAsync(newTask, ct);
				await _uow.SaveChangesAsync(ct);
				importedTasks.Add(newTask.Id);
			}
			catch (Exception ex) { errors.Add($"task '{taskDto.Title}': {ex.Message}"); }
		}
		return new ProjectImportResult(
			1,  // project 已存在, 不再创建
			importedEpics.Count,
			importedStories.Count,
			importedTasks.Count,
			importedEpics.Count + importedStories.Count + importedTasks.Count,  // Imported (sum of all)
			errors.Count,
			errors);
	}

	// ===================== P5: sprint burndown + sprint tasks (BFF module 5, 2026-08-23) =====================

	public async Task<SprintBurndownDto?> GetSprintBurndownAsync(int sprintId, int days, CancellationToken ct = default)
	{
		var sprint = await _sprints.GetByIdAsync(sprintId, ct);
		if (sprint is null) return null;
		if (days <= 0) days = 14;
		var tasks = await _tasks.ListAsync(t => t.SprintId == sprintId, ct);
		var total = tasks.Count;
		var start = sprint.StartDate ?? sprint.CreatedAt;
		var today = DateTime.UtcNow.Date;
		var points = new List<SprintBurndownPoint>();
		for (int i = 0; i < days; i++)
		{
			var day = start.Date.AddDays(i);
			if (day > today) break;
			// actual remaining: 任务在 day 之前没 done
			var actualRemaining = tasks.Count(t => t.UpdatedAt.Date >= day || t.Status != "done");
			// 简化: actual = total - done count
			var doneCount = tasks.Count(t => t.Status == "done" && t.UpdatedAt.Date <= day);
			actualRemaining = total - doneCount;
			// ideal 线性
			var idealRemaining = (int)Math.Round(total * (1.0 - (double)(i + 1) / days));
			points.Add(new SprintBurndownPoint(day, Math.Max(0, idealRemaining), Math.Max(0, actualRemaining)));
		}
		var burnRate = points.Count > 1
			? (points[0].ActualRemaining - points[^1].ActualRemaining) / (double)points.Count
			: 0;
		return new SprintBurndownDto(sprintId, total, points, burnRate);
	}

	public async Task<(IReadOnlyList<TaskItemDto> Items, int Total)> ListSprintTasksAsync(
		int sprintId, string? status, int limit, int offset, CancellationToken ct = default)
	{
		var all = await _tasks.ListAsync(t => t.SprintId == sprintId, ct);
		var filtered = string.IsNullOrWhiteSpace(status) ? all : all.Where(t => t.Status == status).ToList();
		var page = filtered.Skip(offset).Take(limit).ToList();
		return (page.Select(ToTaskDto).ToList(), filtered.Count);
	}
}
