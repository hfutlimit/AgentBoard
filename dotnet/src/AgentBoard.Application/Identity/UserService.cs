// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Identity.Dtos;
using AgentBoard.Domain.Common;
using AgentBoard.Domain.Identity;

namespace AgentBoard.Application.Identity;

/// <summary>
/// Default <see cref="IUserService"/> implementation. Owns the rules for
/// password verification and uniqueness, but does not deal with HTTP
/// concerns (that is the Provider's job).
/// </summary>
public sealed class UserService : IUserService
{
    private readonly IUserRepository _users;
    private readonly IUnitOfWork _uow;
    private readonly IClock _clock;
    private readonly IPasswordHasher _hasher;

    public UserService(IUserRepository users, IUnitOfWork uow, IClock clock, IPasswordHasher hasher)
    {
        _users = users ?? throw new ArgumentNullException(nameof(users));
        _uow = uow ?? throw new ArgumentNullException(nameof(uow));
        _clock = clock ?? throw new ArgumentNullException(nameof(clock));
        _hasher = hasher ?? throw new ArgumentNullException(nameof(hasher));
    }

    public Task<User?> GetByIdAsync(int id, CancellationToken ct = default) =>
        _users.GetByIdAsync(id, ct);

    public Task<User?> GetByUsernameAsync(string username, CancellationToken ct = default) =>
        _users.GetByUsernameAsync(username, ct);

    public async Task<UserDto> CreateAsync(CreateUserRequest request, CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(request);
        if (string.IsNullOrWhiteSpace(request.Username))
            throw new InvalidValueException("username is required");
        if (string.IsNullOrWhiteSpace(request.Password) || request.Password.Length < 8)
            throw new InvalidValueException("password must be at least 8 characters");

        if (await _users.ExistsByUsernameAsync(request.Username, ct))
            throw new DuplicateException($"username '{request.Username}' already exists");

        // Password hashing mirrors FastAPI's PBKDF2-SHA256 scheme so the
        // .NET BFF authenticates against the same users table.
        var passwordHash = _hasher.Hash(request.Password);
        var user = User.Create(request.Username, passwordHash, request.IsAdmin, _clock.UtcNow);
        await _users.AddAsync(user, ct);
        await _uow.SaveChangesAsync(ct);
        return ToDto(user);
    }

    public async Task UpdateProfileAsync(int id, string? displayName, string? email,
        string? avatarUrl, CancellationToken ct = default)
    {
        var user = await _users.GetByIdAsync(id, ct)
            ?? throw new NotFoundException(nameof(User), id);
        user.UpdateProfile(displayName, email, avatarUrl, _clock.UtcNow, id);
        _users.Update(user);
        await _uow.SaveChangesAsync(ct);
    }

    public async Task ChangePasswordAsync(int id, string currentPassword, string newPassword,
        CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(newPassword) || newPassword.Length < 8)
            throw new InvalidValueException("new password must be at least 8 characters");
        if (!await VerifyPasswordAsync(id, currentPassword, ct))
            throw new InvalidValueException("current password is incorrect");

        var user = await _users.GetByIdAsync(id, ct)
            ?? throw new NotFoundException(nameof(User), id);
        // Re-hash with the same PBKDF2 scheme FastAPI uses.
        user.ChangePassword(_hasher.Hash(newPassword), _clock.UtcNow, id);
        _users.Update(user);
        await _uow.SaveChangesAsync(ct);
    }

    public async Task<bool> VerifyPasswordAsync(int id, string password, CancellationToken ct = default)
    {
        var user = await _users.GetByIdAsync(id, ct);
        return user is not null && _hasher.Verify(user.PasswordHash, password);
    }

    private static UserDto ToDto(User u) =>
        new(u.Id, u.Username, u.DisplayName, u.Email, u.AvatarUrl, u.IsAdmin, u.CreatedAt, u.UpdatedAt);
}
