// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Abstractions;

/// <summary>
/// Caller context resolved from the bearer token / API key. The HttpContext
/// binding is wired in the API layer; here we only need the values.
/// </summary>
public interface ICurrentUser
{
    int? UserId { get; }
    string? Username { get; }
    bool IsAdmin { get; }
    IReadOnlyList<string> ApiKeyPermissions { get; }
}
