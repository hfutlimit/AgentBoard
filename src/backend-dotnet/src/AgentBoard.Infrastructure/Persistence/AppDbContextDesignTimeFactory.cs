// SPDX-License-Identifier: MIT
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Design;

namespace AgentBoard.Infrastructure.Persistence;

/// <summary>
/// Used by <c>dotnet ef ...</c> at design time to construct an
/// <see cref="AppDbContext"/> without booting the API host. The connection
/// string here is throwaway — migrations generated from this factory are
/// committed to source and applied to the real MariaDB via Alembic anyway.
///
/// Keeping this factory free of the AddInfrastructure() DI graph means we
/// can generate migrations even when the API host fails to start
/// (e.g. missing AGENTBOARD_DB_URL during CI).
/// </summary>
public sealed class AppDbContextDesignTimeFactory : IDesignTimeDbContextFactory<AppDbContext>
{
    public AppDbContext CreateDbContext(string[] args)
    {
        var options = new DbContextOptionsBuilder<AppDbContext>()
            .UseSqlite("Data Source=ef-design-time.db;Mode=Memory;Cache=Shared")
            .Options;
        return new AppDbContext(options);
    }
}
