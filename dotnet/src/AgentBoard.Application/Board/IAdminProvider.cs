// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board.Dtos;

namespace AgentBoard.Application.Board;

/// <summary>Admin operations. Mirrors FastAPI admin router.</summary>
public interface IAdminProvider : IProvider
{
    Task<IReadOnlyList<AdminUserDto>> ListUsersAsync(CancellationToken ct = default);
    Task<bool> SetUserAdminAsync(int userId, bool isAdmin, CancellationToken ct = default);
    Task<IReadOnlyList<AdminProjectDto>> ListAllProjectsAsync(CancellationToken ct = default);
    Task<bool> DeleteProjectAsync(int projectId, CancellationToken ct = default);
}
