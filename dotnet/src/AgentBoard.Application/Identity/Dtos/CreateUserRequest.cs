// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Identity.Dtos;

/// <summary>Payload for creating a new user. The Service is responsible for
/// hashing the password before persisting.</summary>
public sealed record CreateUserRequest(
    string Username,
    string Password,
    bool IsAdmin = false);
