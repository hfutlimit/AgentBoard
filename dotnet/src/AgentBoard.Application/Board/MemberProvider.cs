// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board.Dtos;
using AgentBoard.Domain.Common;
using AgentBoard.Domain.Entities;

namespace AgentBoard.Application.Board;

/// <summary>Project member management. Mirrors FastAPI members router.</summary>
public sealed class MemberProvider : IMemberProvider
{
    private readonly IProjectMemberRepository _members;
    private readonly IUserRepository _users;
    private readonly IUnitOfWork _uow;

    public MemberProvider(IProjectMemberRepository members, IUserRepository users, IUnitOfWork uow)
    {
        _members = members ?? throw new ArgumentNullException(nameof(members));
        _users = users ?? throw new ArgumentNullException(nameof(users));
        _uow = uow ?? throw new ArgumentNullException(nameof(uow));
    }

    public async Task<ProjectMemberDto> InviteMemberAsync(
        int projectId, int? userId, string? username, string? role, CancellationToken ct = default)
    {
        // Resolve user by userId or username.
        Domain.Identity.User? user = null;
        if (userId is not null)
        {
            user = await _users.GetByIdAsync(userId.Value, ct)
                ?? throw new NotFoundException("user", userId.Value);
        }
        else if (!string.IsNullOrWhiteSpace(username))
        {
            user = await _users.GetByUsernameAsync(username.Trim(), ct)
                ?? throw new NotFoundException("user", username.Trim());
        }
        else
        {
            throw new InvalidValueException("either user_id or username is required");
        }

        // Check not already a member.
        var existing = await _members.ListAsync(
            m => m.ProjectId == projectId && m.UserId == user.Id, ct);
        if (existing.Count > 0)
            throw new DuplicateException($"user {user.Id} is already a member of project {projectId}");

        var member = new ProjectMember
        {
            ProjectId = projectId,
            UserId = user.Id,
            Role = (role ?? "member").Trim(),
            JoinedAt = DateTime.UtcNow,
        };

        await _members.AddAsync(member, ct);
        await _uow.SaveChangesAsync(ct);

        return new ProjectMemberDto(member.Id, member.ProjectId, member.UserId, member.Role, member.JoinedAt, user.Username);
    }

    public async Task<bool> RemoveMemberAsync(int projectId, int userId, CancellationToken ct = default)
    {
        var members = await _members.ListAsync(
            m => m.ProjectId == projectId && m.UserId == userId, ct);
        var member = members.FirstOrDefault();
        if (member is null) return false;

        _members.Remove(member);
        await _uow.SaveChangesAsync(ct);
        return true;
    }

    public async Task<ProjectMemberDto?> UpdateMemberRoleAsync(
        int projectId, int userId, string? role, CancellationToken ct = default)
    {
        var members = await _members.ListAsync(
            m => m.ProjectId == projectId && m.UserId == userId, ct);
        var member = members.FirstOrDefault();
        if (member is null) return null;

        role = (role ?? string.Empty).Trim();
        if (role.Length == 0)
            throw new InvalidValueException("role must not be empty");

        member.Role = role;
        _members.Update(member);
        await _uow.SaveChangesAsync(ct);

        var user = await _users.GetByIdAsync(userId, ct);
        return new ProjectMemberDto(member.Id, member.ProjectId, member.UserId, member.Role, member.JoinedAt, user?.Username);
    }
}
