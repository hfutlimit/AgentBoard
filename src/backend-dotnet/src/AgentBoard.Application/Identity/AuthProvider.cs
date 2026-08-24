// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Identity.Dtos;
using AgentBoard.Domain.Common;

namespace AgentBoard.Application.Identity;

/// <summary>
/// Auth flow composition. Wraps <see cref="IUserService"/> with a
/// (placeholder) token issuer. Stage 1 replaces the issuer with the
/// real HMAC-based <c>v1.&lt;payload&gt;.&lt;sig&gt;</c> implementation
/// that mirrors FastAPI exactly.
/// </summary>
public sealed class AuthProvider : IAuthProvider
{
    private readonly IUserService _users;
    private readonly IClock _clock;
    private readonly ITokenService _tokens;

    public AuthProvider(IUserService users, IClock clock, ITokenService tokens)
    {
        _users = users ?? throw new ArgumentNullException(nameof(users));
        _clock = clock ?? throw new ArgumentNullException(nameof(clock));
        _tokens = tokens ?? throw new ArgumentNullException(nameof(tokens));
    }

    public async Task<AuthSessionDto> LoginAsync(string username, string password, CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(username) || string.IsNullOrWhiteSpace(password))
            throw new InvalidValueException("username and password are required");

        var user = await _users.GetByUsernameAsync(username, ct)
            ?? throw new InvalidValueException("invalid credentials");
        if (!await _users.VerifyPasswordAsync(user.Id, password, ct))
            throw new InvalidValueException("invalid credentials");

        // Stateless HMAC token (v1.{uid}.{exp}.{sig}) — wire-compatible with
        // the FastAPI backend. The middleware re-validates it on /api/auth/me.
        var token = _tokens.IssueToken(user.Id);
        return new AuthSessionDto(user.Id, user.Username, token);
    }

    public async Task<UserDto> GetCurrentAsync(int uid, CancellationToken ct = default)
    {
        var user = await _users.GetByIdAsync(uid, ct)
            ?? throw new NotFoundException(nameof(Domain.Identity.User), uid);
        return new UserDto(
            user.Id, user.Username, user.DisplayName, user.Email, user.AvatarUrl,
            user.IsAdmin, user.CreatedAt, user.UpdatedAt);
    }

    public Task ChangePasswordAsync(int uid, string currentPassword, string newPassword, CancellationToken ct = default) =>
        _users.ChangePasswordAsync(uid, currentPassword, newPassword, ct);

    public async Task<AuthSessionDto> RegisterAsync(string? username, string? password, CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(username))
            throw new InvalidValueException("username is required");
        if (string.IsNullOrWhiteSpace(password) || password.Length < 8)
            throw new InvalidValueException("password must be at least 8 characters");

        var request = new CreateUserRequest(username.Trim(), password);
        var userDto = await _users.CreateAsync(request, ct);

        // Issue token for the newly created user.
        var token = _tokens.IssueToken(userDto.Id);
        return new AuthSessionDto(userDto.Id, userDto.Username, token);
    }

    public async Task<UserDto> UpdateProfileAsync(int userId, string? displayName, string? email, string? avatarUrl, CancellationToken ct = default)
    {
        await _users.UpdateProfileAsync(userId, displayName, email, avatarUrl, ct);
        return await GetCurrentAsync(userId, ct);
    }
}
