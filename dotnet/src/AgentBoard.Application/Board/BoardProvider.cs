// SPDX-License-Identifier: MIT
using System.Collections.Generic;
using System.Linq;
using System.Linq.Expressions;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board.Dtos;
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
        // Exactly one of task/story/epic must be set — mirrors FastAPI _comment_target.
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
    {
        name = (name ?? string.Empty).Trim();
        if (name.Length == 0 || name.Length > 200)
            throw new InvalidValueException("name must be 1-200 characters");
        if (!string.IsNullOrWhiteSpace(key))
        {
            key = key.Trim();
            if (key.Length > 20)
                throw new InvalidValueException("key must be at most 20 characters");
        }
        else
        {
            key = null;
        }

        var project = new Project
        {
            Name = name,
            Key = key,
            Description = description ?? string.Empty,
            IsPrivate = true, // FastAPI forces invite-only for all new projects
            CreatedAt = DateTime.UtcNow,
        };

        if (project.Key is not null)
        {
            var clash = await _projects.ListAsync(p => p.Key == project.Key, ct);
            if (clash.Count != 0)
                throw new DuplicateException($"project key '{project.Key}' already exists");
        }

        await _projects.AddAsync(project, ct);
        await _uow.SaveChangesAsync(ct);

        if (currentUserId is not null)
        {
            await _members.AddAsync(new ProjectMember
            {
                ProjectId = project.Id,
                UserId = currentUserId.Value,
                Role = "owner",
                JoinedAt = DateTime.UtcNow,
            }, ct);
            await _uow.SaveChangesAsync(ct);
        }

        return ToProjectDto(project);
    }

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
    {
        var project = await _projects.GetByIdAsync(id, ct);
        if (project is null) return false;

        // Cascade the board hierarchy (mirrors FastAPI delete_project's core scope).
        // Documents / Proposals / Schedules subsystems are out of .NET scope (P3) and
        // are a known gap noted in the migration plan.
        var epics = await _epics.ListAsync(e => e.ProjectId == id, ct);
        var epicIds = epics.Select(e => e.Id).ToHashSet();
        var stories = epicIds.Count == 0
            ? Array.Empty<Story>()
            : await _stories.ListAsync(s => epicIds.Contains(s.EpicId), ct);
        var storyIds = stories.Select(s => s.Id).ToHashSet();
        var tasks = storyIds.Count == 0
            ? Array.Empty<TaskItem>()
            : await _tasks.ListAsync(t => t.StoryId != null && storyIds.Contains(t.StoryId.Value), ct);

        foreach (var t in tasks)
            _comments.RemoveRange(await _comments.ListAsync(c => c.TaskId == t.Id, ct));
        foreach (var s in stories)
            _comments.RemoveRange(await _comments.ListAsync(c => c.StoryId == s.Id, ct));
        foreach (var e in epics)
            _comments.RemoveRange(await _comments.ListAsync(c => c.EpicId == e.Id, ct));

        _tasks.RemoveRange(tasks);
        _stories.RemoveRange(stories);
        _epics.RemoveRange(epics);
        _members.RemoveRange(await _members.ListAsync(m => m.ProjectId == id, ct));
        _projects.Remove(project);

        await _uow.SaveChangesAsync(ct);
        return true;
    }

    // ===================== P1: dashboard / board reads =====================

    /// <summary>Cross-project overview. Admin sees all; member sees own; anon sees empty.</summary>
    public async Task<OverviewDto> GetOverviewAsync(int? currentUserId, bool isAdmin, CancellationToken ct = default)
    {
        if (currentUserId is null)
            return EmptyOverview();

        List<int> projectIds = isAdmin
            ? (await _projects.ListAsync(ct: ct)).Select(p => p.Id).ToList()
            : (await _members.ListAsync(m => m.UserId == currentUserId, ct)).Select(m => m.ProjectId).ToList();

        if (projectIds.Count == 0)
            return EmptyOverview();

        var epics = await _epics.ListAsync(e => projectIds.Contains(e.ProjectId), ct);
        var epicIds = epics.Select(e => e.Id).ToHashSet();
        var stories = epicIds.Count > 0
            ? await _stories.ListAsync(s => epicIds.Contains(s.EpicId), ct)
            : new List<Story>();
        var tasks = await _tasks.ListAsync(t => projectIds.Contains(t.ProjectId), ct);

        var epicCount = epics.Count;
        var storyCount = stories.Count;
        var taskCount = tasks.Count;
        var doneTasks = tasks.Count(t => t.Status == "done");

        var perProject = tasks.GroupBy(t => t.ProjectId).ToDictionary(g => g.Key, g => g.Count());
        var perProjectDone = tasks.Where(t => t.Status == "done")
            .GroupBy(t => t.ProjectId).ToDictionary(g => g.Key, g => g.Count());

        var projects = (await _projects.ListAsync(p => projectIds.Contains(p.Id), ct))
            .Select(p =>
            {
                var total = perProject.GetValueOrDefault(p.Id, 0);
                var done = perProjectDone.GetValueOrDefault(p.Id, 0);
                return new OverviewProjectProgress(
                    p.Id, p.Name, total, done,
                    total == 0 ? 0 : (int)Math.Round(done * 100.0 / total));
            })
            .OrderByDescending(x => x.Total).ThenBy(x => x.Id)
            .ToList();

        // ALL_STATUSES order from FastAPI core.common.enums.Status (5-state).
        var allStatuses = new[] { "todo", "in_progress", "in_review", "done", "blocked" };
        var statusCounts = tasks.GroupBy(t => t.Status).ToDictionary(g => g.Key, g => g.Count());
        var statusDistribution = allStatuses
            .Select(s => new StatusCount(s, statusCounts.GetValueOrDefault(s, 0)))
            .ToList();

        var now = DateTime.Now;
        var sevenDaysAgo = now.Date.AddDays(-6);
        var dayCounts = tasks
            .Where(t => t.UpdatedAt >= sevenDaysAgo)
            .GroupBy(t => t.UpdatedAt.Date)
            .ToDictionary(g => g.Key, g => g.Count());
        var activity7d = Enumerable.Range(0, 7)
            .Select(i =>
            {
                var day = sevenDaysAgo.AddDays(i).Date;
                return new DayCount(day.ToString("yyyy-MM-dd"), dayCounts.GetValueOrDefault(day, 0));
            })
            .ToList();

        return new OverviewDto(
            new OverviewCounts(projectIds.Count, epicCount, storyCount, taskCount, doneTasks),
            projects, statusDistribution, activity7d);
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

        var all = (await _members.ListAsync(m => m.ProjectId == projectId, ct))
            .OrderByDescending(m => m.JoinedAt)
            .ToList();
        var total = all.Count;
        var page = all.Skip(offset).Take(limit).ToList();

        var items = new List<ProjectMemberDto>();
        foreach (var m in page)
        {
            var u = await _users.GetByIdAsync(m.UserId, ct);
            items.Add(new ProjectMemberDto(m.Id, m.ProjectId, m.UserId, m.Role, m.JoinedAt, u?.Username));
        }
        return new ProjectMembersResult(items, total);
    }

    public async Task<NotificationsResult> ListNotificationsAsync(
        int userId, int limit, int offset, bool unreadOnly, CancellationToken ct = default)
    {
        var all = (await _notifications.ListAsync(n => n.UserId == userId, ct))
            .Where(n => !unreadOnly || !n.IsRead)
            .OrderByDescending(n => n.CreatedAt)
            .ToList();
        var total = all.Count;
        var page = all.Skip(offset).Take(limit).ToList();
        var items = page
            .Select(n => new NotificationDto(n.Id, n.UserId, n.Type, n.Title, n.Content, n.IsRead, n.Link, n.CreatedAt))
            .ToList();
        return new NotificationsResult(items, total);
    }

    public async Task<int> GetUnreadNotificationCountAsync(int userId, CancellationToken ct = default)
    {
        var items = await _notifications.ListAsync(n => n.UserId == userId, ct);
        return items.Count(n => !n.IsRead);
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

    private static CommentDto ToCommentDto(Comment c) =>
        new(c.Id, c.TaskId, c.StoryId, c.EpicId, c.Author, c.Content, c.CreatedAt, c.UpdatedAt);

    private static KanbanTaskDto ToKanbanTaskDto(TaskItem t) =>
        new(t.Id, t.Type, t.Title, t.Status, t.Priority, t.AssigneeId, t.Estimate);

    private static KanbanStoryDto ToKanbanStoryDto(Story s, IReadOnlyList<KanbanTaskDto> tasks) =>
        new(s.Id, s.EpicId, s.Title, s.Description, s.Status, s.NeedsDesign, s.InKanban, tasks, s.CreatedAt);
}
