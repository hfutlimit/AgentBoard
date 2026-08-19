// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Identity;
using Microsoft.EntityFrameworkCore;

namespace AgentBoard.Infrastructure.Persistence.Repositories;

/// <summary>User-specific repository. Exists primarily as a seam for future
/// custom queries; the basic CRUD surface is inherited from
/// <see cref="Repository{T}"/>.</summary>
public interface IUserRepository : AgentBoard.Application.Abstractions.IRepository<User>
{
    Task<User?> GetByUsernameAsync(string username, CancellationToken ct = default);
    Task<bool> ExistsByUsernameAsync(string username, CancellationToken ct = default);
}

public sealed class UserRepository : Repository<User>, IUserRepository
{
    public UserRepository(AppDbContext db) : base(db) { }
    protected override DbSet<User> Set => Db.Users;

    public Task<User?> GetByUsernameAsync(string username, CancellationToken ct = default)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(username);
        return Set.AsNoTracking().FirstOrDefaultAsync(u => u.Username == username, ct);
    }

    public Task<bool> ExistsByUsernameAsync(string username, CancellationToken ct = default)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(username);
        return Set.AsNoTracking().AnyAsync(u => u.Username == username, ct);
    }
}
