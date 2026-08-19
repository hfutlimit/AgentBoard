// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Identity.Dtos;

namespace AgentBoard.Application.Identity;

/// <summary>
/// Cross-Service auth flow: <c>LoginAsync</c> composes
/// <see cref="IUserService.VerifyPasswordAsync"/> with a future JWT issuer
/// (stage 1), and <c>GetCurrentAsync</c> reads the user record for the
/// authenticated caller. Stage 0 ships a stub JWT issuer that returns a
/// deterministic string; stage 1 swaps in the real HMAC implementation
/// that mirrors FastAPI's token format.
/// </summary>
public interface IAuthProvider : IProvider
{
    Task<AuthSessionDto> LoginAsync(string username, string password, CancellationToken ct = default);
    Task<UserDto> GetCurrentAsync(int uid, CancellationToken ct = default);
    Task ChangePasswordAsync(int uid, string currentPassword, string newPassword, CancellationToken ct = default);
}
