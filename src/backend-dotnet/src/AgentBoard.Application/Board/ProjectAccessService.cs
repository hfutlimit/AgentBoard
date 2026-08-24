// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Domain.Common;

namespace AgentBoard.Application.Board;

/// <summary>Central project authorization boundary for write operations.</summary>
public sealed class ProjectAccessService : IProjectAccessService
{
    private readonly IProjectRepository _projects;
    private readonly IProjectMemberRepository _members;
    private readonly IUserRepository _users;
    private readonly ICurrentUser _current;

    public ProjectAccessService(
        IProjectRepository projects,
        IProjectMemberRepository members,
        IUserRepository users,
        ICurrentUser current)
    {
        _projects = projects ?? throw new ArgumentNullException(nameof(projects));
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
}
