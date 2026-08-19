// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Identity.Dtos;

/// <summary>Login response. Mirrors the FastAPI <c>TokenOut</c> shape:
/// <c>{id, username, token}</c> + extra fields the FastAPI version
/// returns.</summary>
public sealed record AuthSessionDto(
    int Id,
    string Username,
    string Token,
    string TokenType = "bearer",
    int ExpiresIn = 0);
