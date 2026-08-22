// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Board.Dtos;

// ProjectMemberDto is defined in DashboardDtas.cs (shared with board reads).

/// <summary>Request body for <c>POST /api/projects/{pid}/members</c>.</summary>
public sealed record MemberInviteRequest(
    int? UserId,
    string? Username,
    string? Role);

/// <summary>Request body for <c>PATCH /api/projects/{pid}/members/{uid}</c>.</summary>
public sealed record MemberRolePatchRequest(
    string? Role);
