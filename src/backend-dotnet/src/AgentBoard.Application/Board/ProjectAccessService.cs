// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Domain.Common;

namespace AgentBoard.Application.Board;

/// <summary>Central project authorization boundary for write operations.</summary>
public sealed class ProjectAccessService : IProjectAccessService
{
    private readonly IProjectRepository _projects;
    private readonly IProjectMemberRepository _members;
    private readonly ICurrentUser _current;

    public ProjectAccessService(
        IProjectRepository projects,
        IProjectMemberRepository members,
        ICurrentUser current)
    {
        _projects = projects ?? throw new ArgumentNullException(nameof(projects));
        _members = members ?? throw new ArgumentNullException(nameof(members));
        _current = current ?? throw new ArgumentNullException(nameof(current));
    }

    public async Task RequireMemberManagementAsync(int projectId, CancellationToken ct = default)
    {
        if (await _projects.GetByIdAsync(projectId, ct) is null)
            throw new NotFoundException($"project {projectId} not found");
        if (_current.IsAdmin) return;
        if (_current.UserId is not { } userId)
            throw new UnauthorizedException();

        var actor = (await _members.ListAsync(
            m => m.ProjectId == projectId && m.UserId == userId, ct)).FirstOrDefault();
        if (actor is null || actor.Role is not ("owner" or "admin"))
            throw new ForbiddenException("project owner or admin permission required");
    }
}
