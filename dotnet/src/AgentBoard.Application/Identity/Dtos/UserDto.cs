// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Identity.Dtos;

/// <summary>User record exposed to the API. Mirrors the FastAPI <c>UserOut</c>
/// schema 1:1 (id / username / display_name / email / is_admin / timestamps).</summary>
public sealed record UserDto(
    int Id,
    string Username,
    string? DisplayName,
    string? Email,
    string? AvatarUrl,
    bool IsAdmin,
    DateTime CreatedAt,
    DateTime UpdatedAt);
