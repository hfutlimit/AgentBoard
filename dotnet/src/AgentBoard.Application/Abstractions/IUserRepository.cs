// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Identity;

namespace AgentBoard.Application.Abstractions;

/// <summary>
/// User-specific repository contract. Lives in the Application layer per
/// the Clean Architecture convention: the interface is a domain seam that
/// the Application defines, and the Infrastructure provides the EF Core
/// implementation.
/// </summary>
public interface IUserRepository : IRepository<User>
{
    Task<User?> GetByUsernameAsync(string username, CancellationToken ct = default);
    Task<bool> ExistsByUsernameAsync(string username, CancellationToken ct = default);
}
