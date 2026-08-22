// SPDX-License-Identifier: MIT
using System.Linq;
using System.Linq.Expressions;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board.Dtos;
using AgentBoard.Domain.Entities;

namespace AgentBoard.Application.Board;

public sealed class BoardProvider : IBoardProvider
{
    private readonly IProjectRepository _projects;
    private readonly IEpicRepository _epics;
    private readonly IStoryRepository _stories;
    private readonly ITaskItemRepository _tasks;
    private readonly ICommentRepository _comments;

    public BoardProvider(
        IProjectRepository projects,
        IEpicRepository epics,
        IStoryRepository stories,
        ITaskItemRepository tasks,
        ICommentRepository comments)
    {
        _projects = projects ?? throw new ArgumentNullException(nameof(projects));
        _epics = epics ?? throw new ArgumentNullException(nameof(epics));
        _stories = stories ?? throw new ArgumentNullException(nameof(stories));
        _tasks = tasks ?? throw new ArgumentNullException(nameof(tasks));
        _comments = comments ?? throw new ArgumentNullException(nameof(comments));
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
}
