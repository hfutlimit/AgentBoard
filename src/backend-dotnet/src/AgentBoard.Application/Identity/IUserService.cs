// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Identity.Dtos;
using AgentBoard.Domain.Identity;

namespace AgentBoard.Application.Identity;

/// <summary>
/// Operations on the <see cref="User"/> aggregate. All password handling
/// is delegated to an <c>IPasswordHasher</c> implementation registered
/// in Infrastructure (PBKDF2 today; BCrypt/Argon2 is a one-line swap).
/// </summary>
public interface IUserService : IService
{
    Task<User?> GetByIdAsync(int id, CancellationToken ct = default);
    Task<User?> GetByUsernameAsync(string username, CancellationToken ct = default);
    Task<UserDto> CreateAsync(CreateUserRequest request, CancellationToken ct = default);
    Task UpdateProfileAsync(int id, string? displayName, string? email, string? avatarUrl, CancellationToken ct = default);
    Task ChangePasswordAsync(int id, string currentPassword, string newPassword, CancellationToken ct = default);
    Task<bool> VerifyPasswordAsync(int id, string password, CancellationToken ct = default);
}
