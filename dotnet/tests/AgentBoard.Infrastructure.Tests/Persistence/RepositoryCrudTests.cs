// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Identity;
using AgentBoard.Infrastructure.Persistence.Repositories;
using FluentAssertions;

namespace AgentBoard.Infrastructure.Tests.Persistence;

public sealed class RepositoryCrudTests
{
    [Fact]
    public async Task Add_Then_GetById_Returns_Same_Entity()
    {
        using var db = TestDbContextFactory.Create(dbName: nameof(Add_Then_GetById_Returns_Same_Entity));
        var repo = new AgentBoard.Infrastructure.Persistence.Repositories.UserRepository(db);

        var user = User.Create("alice", "hash", false, DateTime.UtcNow);
        await repo.AddAsync(user);
        await db.SaveChangesAsync();

        var loaded = await repo.GetByIdAsync(user.Id);
        loaded.Should().NotBeNull();
        loaded!.Username.Should().Be("alice");
    }

    [Fact]
    public async Task List_Predicates_Only_Return_Matching_Entities()
    {
        using var db = TestDbContextFactory.Create(dbName: nameof(List_Predicates_Only_Return_Matching_Entities));
        var repo = new AgentBoard.Infrastructure.Persistence.Repositories.UserRepository(db);

        await repo.AddRangeAsync(new[]
        {
            User.Create("alice", "h", false, DateTime.UtcNow),
            User.Create("bob",   "h", false, DateTime.UtcNow),
            User.Create("carol", "h", false, DateTime.UtcNow),
        });
        await db.SaveChangesAsync();

        var matches = await repo.ListAsync(u => u.Username.StartsWith("a") || u.Username.StartsWith("c"));
        matches.Should().HaveCount(2);
        matches.Select(u => u.Username).Should().BeEquivalentTo(new[] { "alice", "carol" });
    }

    [Fact]
    public async Task Update_Changes_Tracked_Entity()
    {
        using var db = TestDbContextFactory.Create(dbName: nameof(Update_Changes_Tracked_Entity));
        var repo = new AgentBoard.Infrastructure.Persistence.Repositories.UserRepository(db);
        var user = User.Create("alice", "h", false, DateTime.UtcNow);
        await repo.AddAsync(user);
        await db.SaveChangesAsync();

        user.UpdateProfile("Alice the Admin", "alice@example.com", null, DateTime.UtcNow, user.Id);
        repo.Update(user);
        await db.SaveChangesAsync();

        var loaded = await repo.GetByIdAsync(user.Id);
        loaded!.DisplayName.Should().Be("Alice the Admin");
        loaded.Email.Should().Be("alice@example.com");
    }

    [Fact]
    public async Task Remove_Performs_Hard_Delete_In_InMemory()
    {
        // In-memory provider has no soft-delete intercept by default;
        // we assert hard-delete semantics here and the soft-delete
        // interceptor behaviour is covered separately.
        using var db = TestDbContextFactory.Create(dbName: nameof(Remove_Performs_Hard_Delete_In_InMemory));
        var repo = new AgentBoard.Infrastructure.Persistence.Repositories.UserRepository(db);
        var user = User.Create("alice", "h", false, DateTime.UtcNow);
        await repo.AddAsync(user);
        await db.SaveChangesAsync();

        repo.Remove(user);
        await db.SaveChangesAsync();

        var loaded = await repo.GetByIdAsync(user.Id);
        loaded.Should().BeNull();
    }

    [Fact]
    public async Task Count_Returns_Total()
    {
        using var db = TestDbContextFactory.Create(dbName: nameof(Count_Returns_Total));
        var repo = new AgentBoard.Infrastructure.Persistence.Repositories.UserRepository(db);
        for (var i = 0; i < 5; i++)
            await repo.AddAsync(User.Create($"u{i}", "h", false, DateTime.UtcNow));
        await db.SaveChangesAsync();

        (await repo.CountAsync()).Should().Be(5);
        (await repo.CountAsync(u => u.Username == "u2")).Should().Be(1);
    }
}
