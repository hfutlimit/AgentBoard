// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board.Dtos;

namespace AgentBoard.Application.Board;

/// <summary>Project member management. Mirrors FastAPI members router.</summary>
public interface IMemberProvider : IProvider
{
    Task<ProjectMemberDto> InviteMemberAsync(int projectId, InviteMemberRequest request, CancellationToken ct = default);
    Task<bool> RemoveMemberAsync(int projectId, int userId, CancellationToken ct = default);
    Task<ProjectMemberDto?> UpdateMemberRoleAsync(int projectId, int userId, string? role, CancellationToken ct = default);
}
