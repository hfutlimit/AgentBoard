// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Identity;

/// <summary>
/// Issues and validates the stateless bearer token used by the .NET BFF.
/// The wire format mirrors the FastAPI backend so a token is portable
/// across both stacks.
/// </summary>
public interface ITokenService
{
    /// <summary>Issues a <c>v1.{user_id}.{expires_at}.{sig}</c> token.</summary>
    string IssueToken(int userId);

    /// <summary>Validates a token and returns the user id, or null if the
    /// token is missing, malformed, expired, or the signature is invalid.</summary>
    int? ValidateToken(string? token);
}
