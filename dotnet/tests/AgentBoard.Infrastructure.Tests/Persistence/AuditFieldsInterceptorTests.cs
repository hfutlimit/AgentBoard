// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Identity;
using AgentBoard.Infrastructure.Persistence.Repositories;
using FluentAssertions;

namespace AgentBoard.Infrastructure.Tests.Persistence;

public sealed class AuditFieldsInterceptorTests
{
    [Fact]
    public async Task Insert_Stamps_CreatedAt_And_UpdatedAt_From_Clock()
    {
        var (db, _, _, _, clock) = TestDbContextFactory.CreateWithInterceptors(nameof(Insert_Stamps_CreatedAt_And_UpdatedAt_From_Clock));
        var repo = new UserRepository(db);

        var t0 = clock.UtcNow;
        var user = User.Create("alice", "h", false, t0);
        await repo.AddAsync(user);
        await db.SaveChangesAsync();

        user.CreatedAt.Should().Be(t0);
        user.UpdatedAt.Should().Be(t0);
        user.CreatedBy.Should().Be(42);
        user.UpdatedBy.Should().Be(42);
    }

    [Fact]
    public async Task Update_Advances_UpdatedAt_But_Not_CreatedAt()
    {
        var (db, _, _, _, clock) = TestDbContextFactory.CreateWithInterceptors(nameof(Update_Advances_UpdatedAt_But_Not_CreatedAt));
        var repo = new UserRepository(db);

        var t0 = clock.UtcNow;
        var user = User.Create("alice", "h", false, t0);
        await repo.AddAsync(user);
        await db.SaveChangesAsync();

        clock.Advance(TimeSpan.FromMinutes(5));
        user.UpdateProfile("Alice", "alice@example.com", null, clock.UtcNow, user.Id);
        repo.Update(user);
        await db.SaveChangesAsync();

        user.CreatedAt.Should().Be(t0);
        user.UpdatedAt.Should().Be(t0.AddMinutes(5));
    }
}
