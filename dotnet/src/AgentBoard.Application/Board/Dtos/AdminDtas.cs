// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Board.Dtos;

/// <summary>Admin user view. Mirrors FastAPI admin user list.</summary>
public sealed record AdminUserDto(
    int Id,
    string Username,
    string? DisplayName,
    string? Email,
    bool IsAdmin,
    DateTime CreatedAt);

/// <summary>Admin project view. Mirrors FastAPI admin project list.</summary>
public sealed record AdminProjectDto(
    int Id,
    string Name,
    string? Key,
    bool IsPrivate,
    bool IsArchived,
    DateTime CreatedAt);
