// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Domain.Common;
using AgentBoard.Domain.Entities;

namespace AgentBoard.Application.Board;

/// <summary>Central project and aggregate-resource authorization boundary.</summary>
public sealed class ProjectAccessService : IProjectAccessService
{
    private readonly IProjectRepository _projects;
    private readonly IEpicRepository _epics;
    private readonly IStoryRepository _stories;
    private readonly ITaskItemRepository _tasks;
    private readonly ICommentRepository _comments;
    private readonly IAttachmentRepository _attachments;
    private readonly IProjectMemberRepository _members;
    private readonly IUserRepository _users;
    private readonly ICurrentUser _current;

    public ProjectAccessService(
        IProjectRepository projects,
        IEpicRepository epics,
        IStoryRepository stories,
        ITaskItemRepository tasks,
        ICommentRepository comments,
        IAttachmentRepository attachments,
        IProjectMemberRepository members,
        IUserRepository users,
        ICurrentUser current)
    {
        _projects = projects ?? throw new ArgumentNullException(nameof(projects));
        _epics = epics ?? throw new ArgumentNullException(nameof(epics));
        _stories = stories ?? throw new ArgumentNullException(nameof(stories));
        _tasks = tasks ?? throw new ArgumentNullException(nameof(tasks));
        _comments = comments ?? throw new ArgumentNullException(nameof(comments));
        _attachments = attachments ?? throw new ArgumentNullException(nameof(attachments));
        _members = members ?? throw new ArgumentNullException(nameof(members));
        _users = users ?? throw new ArgumentNullException(nameof(users));
        _current = current ?? throw new ArgumentNullException(nameof(current));
    }

    public async Task RequireProjectReadAsync(int projectId, CancellationToken ct = default)
    {
        await RequireProjectExistsAsync(projectId, ct);
        if (await IsCurrentUserAdminAsync(ct)) return;
        await RequireMembershipAsync(projectId, "project membership required", ct);
    }

    public async Task RequireProjectWriteAsync(int projectId, CancellationToken ct = default)
    {
        await RequireProjectExistsAsync(projectId, ct);
        if (await IsCurrentUserAdminAsync(ct)) return;
        await RequireMembershipAsync(projectId, "project membership required", ct);
    }

    public async Task RequireProjectOwnerAsync(int projectId, CancellationToken ct = default)
    {
        await RequireProjectExistsAsync(projectId, ct);
        if (await IsCurrentUserAdminAsync(ct)) return;
        if (_current.UserId is not { } userId)
            throw new UnauthorizedException();

        var owner = (await _members.ListAsync(
            m => m.ProjectId == projectId && m.UserId == userId && m.Role == "owner", ct)).FirstOrDefault();
        if (owner is null)
            throw new ForbiddenException("project owner permission required");
    }

    public async Task RequireEpicReadAsync(int epicId, CancellationToken ct = default)
    {
        var epic = await _epics.GetByIdAsync(epicId, ct);
        if (epic is null) throw new NotFoundException($"epic {epicId} not found");
        await RequireProjectReadAsync(epic.ProjectId, ct);
    }

    public async Task RequireEpicWriteAsync(int epicId, CancellationToken ct = default)
    {
        var epic = await _epics.GetByIdAsync(epicId, ct);
        if (epic is null) throw new NotFoundException($"epic {epicId} not found");
        await RequireProjectWriteAsync(epic.ProjectId, ct);
    }

    public async Task RequireStoryReadAsync(int storyId, CancellationToken ct = default)
    {
        var story = await _stories.GetByIdAsync(storyId, ct);
        if (story is null) throw new NotFoundException($"story {storyId} not found");
        var epic = await _epics.GetByIdAsync(story.EpicId, ct);
        if (epic is null) throw new NotFoundException($"epic {story.EpicId} not found");
        await RequireProjectReadAsync(epic.ProjectId, ct);
    }

    public async Task RequireStoryWriteAsync(int storyId, CancellationToken ct = default)
    {
        var story = await _stories.GetByIdAsync(storyId, ct);
        if (story is null) throw new NotFoundException($"story {storyId} not found");
        var epic = await _epics.GetByIdAsync(story.EpicId, ct);
        if (epic is null) throw new NotFoundException($"epic {story.EpicId} not found");
        await RequireProjectWriteAsync(epic.ProjectId, ct);
    }

    public async Task RequireTaskReadAsync(int taskId, CancellationToken ct = default)
    {
        var task = await _tasks.GetByIdAsync(taskId, ct);
        if (task is null) throw new NotFoundException($"task {taskId} not found");
        await RequireProjectReadAsync(task.ProjectId, ct);
    }

    public async Task RequireTaskWriteAsync(int taskId, CancellationToken ct = default)
    {
        var task = await _tasks.GetByIdAsync(taskId, ct);
        if (task is null) throw new NotFoundException($"task {taskId} not found");
        await RequireProjectWriteAsync(task.ProjectId, ct);
    }

    public async Task RequireCommentReadAsync(int commentId, CancellationToken ct = default)
    {
        var comment = await _comments.GetByIdAsync(commentId, ct);
        if (comment is null) throw new NotFoundException($"comment {commentId} not found");
        await RequireCommentTargetAsync(comment, write: false, ct);
    }

    public async Task RequireCommentWriteAsync(int commentId, CancellationToken ct = default)
    {
        var comment = await _comments.GetByIdAsync(commentId, ct);
        if (comment is null) throw new NotFoundException($"comment {commentId} not found");
        await RequireCommentTargetAsync(comment, write: true, ct);
    }

    public async Task RequireAttachmentReadAsync(int attachmentId, CancellationToken ct = default)
    {
        var attachment = await _attachments.GetByIdAsync(attachmentId, ct);
        if (attachment is null) throw new NotFoundException($"attachment {attachmentId} not found");
        await RequireTaskReadAsync(attachment.TaskId, ct);
    }

    public async Task RequireAttachmentWriteAsync(int attachmentId, CancellationToken ct = default)
    {
        var attachment = await _attachments.GetByIdAsync(attachmentId, ct);
        if (attachment is null) throw new NotFoundException($"attachment {attachmentId} not found");
        await RequireTaskWriteAsync(attachment.TaskId, ct);
    }

    public async Task RequireMemberManagementAsync(int projectId, CancellationToken ct = default)
    {
        await RequireProjectExistsAsync(projectId, ct);
        if (await IsCurrentUserAdminAsync(ct)) return;
        if (_current.UserId is not { } userId)
            throw new UnauthorizedException();

        var actor = (await _members.ListAsync(
            m => m.ProjectId == projectId && m.UserId == userId, ct)).FirstOrDefault();
        if (actor is null || actor.Role is not ("owner" or "admin"))
            throw new ForbiddenException("project owner or admin permission required");
    }

    public async Task<IReadOnlySet<int>?> GetAccessibleProjectIdsAsync(CancellationToken ct = default)
    {
        if (await IsCurrentUserAdminAsync(ct)) return null;
        if (_current.UserId is not { } userId) return new HashSet<int>();

        return (await _members.ListAsync(m => m.UserId == userId, ct))
            .Select(m => m.ProjectId)
            .ToHashSet();
    }

    public async Task<bool> IsCurrentUserAdminAsync(CancellationToken ct = default)
    {
        if (_current.IsAdmin) return true;
        if (_current.UserId is not { } userId) return false;
        var user = await _users.GetByIdAsync(userId, ct);
        return user?.IsAdmin == true;
    }

    private async Task RequireProjectExistsAsync(int projectId, CancellationToken ct)
    {
        if (await _projects.GetByIdAsync(projectId, ct) is null)
            throw new NotFoundException($"project {projectId} not found");
    }

    private async Task RequireMembershipAsync(int projectId, string message, CancellationToken ct)
    {
        if (_current.UserId is not { } userId)
            throw new UnauthorizedException();

        var member = (await _members.ListAsync(
            m => m.ProjectId == projectId && m.UserId == userId, ct)).FirstOrDefault();
        if (member is null)
            throw new ForbiddenException(message);
    }

    private async Task RequireCommentTargetAsync(Comment comment, bool write, CancellationToken ct)
    {
        if (comment.TaskId is { } taskId)
        {
            if (write) await RequireTaskWriteAsync(taskId, ct);
            else await RequireTaskReadAsync(taskId, ct);
            return;
        }
        if (comment.StoryId is { } storyId)
        {
            if (write) await RequireStoryWriteAsync(storyId, ct);
            else await RequireStoryReadAsync(storyId, ct);
            return;
        }
        if (comment.EpicId is { } epicId)
        {
            if (write) await RequireEpicWriteAsync(epicId, ct);
            else await RequireEpicReadAsync(epicId, ct);
            return;
        }
        throw new NotFoundException($"comment {comment.Id} target not found");
    }
}
