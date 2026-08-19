// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Identity;
using AgentBoard.Infrastructure.Persistence.Repositories;
using FluentAssertions;

namespace AgentBoard.Infrastructure.Tests.Persistence;

public sealed class UserRepositoryTests
{
    [Fact]
    public async Task GetByUsernameAsync_Finds_Match()
    {
        using var db = TestDbContextFactory.Create(dbName: nameof(GetByUsernameAsync_Finds_Match));
        var repo = new AgentBoard.Infrastructure.Persistence.Repositories.UserRepository(db);
        await repo.AddAsync(User.Create("alice", "h", false, DateTime.UtcNow));
        await db.SaveChangesAsync();

        var found = await repo.GetByUsernameAsync("alice");
        found.Should().NotBeNull();
        found!.Username.Should().Be("alice");
    }

    [Fact]
    public async Task GetByUsernameAsync_Returns_Null_When_Missing()
    {
        using var db = TestDbContextFactory.Create(dbName: nameof(GetByUsernameAsync_Returns_Null_When_Missing));
        var repo = new AgentBoard.Infrastructure.Persistence.Repositories.UserRepository(db);

        var found = await repo.GetByUsernameAsync("ghost");
        found.Should().BeNull();
    }

    [Fact]
    public async Task ExistsByUsernameAsync_Detects_Collision()
    {
        using var db = TestDbContextFactory.Create(dbName: nameof(ExistsByUsernameAsync_Detects_Collision));
        var repo = new AgentBoard.Infrastructure.Persistence.Repositories.UserRepository(db);
        await repo.AddAsync(User.Create("alice", "h", false, DateTime.UtcNow));
        await db.SaveChangesAsync();

        (await repo.ExistsByUsernameAsync("alice")).Should().BeTrue();
        (await repo.ExistsByUsernameAsync("bob")).Should().BeFalse();
    }
}
