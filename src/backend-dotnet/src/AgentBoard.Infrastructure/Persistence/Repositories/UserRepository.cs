// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Domain.Identity;
using Microsoft.EntityFrameworkCore;

namespace AgentBoard.Infrastructure.Persistence.Repositories;

/// <summary>EF Core implementation of <see cref="IUserRepository"/>. The
/// interface lives in the Application layer (Clean Architecture); this
/// file is the only place that knows about <c>AppDbContext</c>.</summary>
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
