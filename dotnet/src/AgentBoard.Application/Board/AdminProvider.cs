// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board.Dtos;
using AgentBoard.Domain.Common;
using AgentBoard.Domain.Identity;

namespace AgentBoard.Application.Board;

/// <summary>Admin operations. Mirrors FastAPI admin router.</summary>
public sealed class AdminProvider : IAdminProvider
{
    private readonly IUserRepository _users;
    private readonly IProjectRepository _projects;
    private readonly IUnitOfWork _uow;

    public AdminProvider(IUserRepository users, IProjectRepository projects, IUnitOfWork uow)
    {
        _users = users ?? throw new ArgumentNullException(nameof(users));
        _projects = projects ?? throw new ArgumentNullException(nameof(projects));
        _uow = uow ?? throw new ArgumentNullException(nameof(uow));
    }

    public async Task<IReadOnlyList<AdminUserDto>> ListUsersAsync(CancellationToken ct = default)
    {
        var items = await _users.ListAsync(ct: ct);
        return items.Select(u => new AdminUserDto(u.Id, u.Username, null, null, u.IsAdmin, u.CreatedAt)).ToList();
    }

    public async Task<bool> SetUserAdminAsync(int userId, bool isAdmin, CancellationToken ct = default)
    {
        var user = await _users.GetByIdAsync(userId, ct);
        if (user is null) return false;
        user.SetAdminStatus(isAdmin, DateTime.UtcNow, userId);
        _users.Update(user);
        await _uow.SaveChangesAsync(ct);
        return true;
    }

    public async Task<IReadOnlyList<AdminProjectDto>> ListAllProjectsAsync(CancellationToken ct = default)
    {
        var items = await _projects.ListAsync(ct: ct);
        return items.Select(p => new AdminProjectDto(p.Id, p.Name, p.Key, p.IsPrivate, p.IsArchived, p.CreatedAt)).ToList();
    }

    public async Task<bool> DeleteProjectAsync(int projectId, CancellationToken ct = default)
    {
        var p = await _projects.GetByIdAsync(projectId, ct);
        if (p is null) return false;
        _projects.Remove(p);
        await _uow.SaveChangesAsync(ct);
        return true;
    }
}
