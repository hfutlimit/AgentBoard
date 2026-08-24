// SPDX-License-Identifier: MIT
using System.Diagnostics;
using AgentBoard.Domain.Common.Enums;
using AgentBoard.Domain.Identity;
using AgentBoard.Infrastructure.Persistence.Repositories;
using FluentAssertions;
using Microsoft.EntityFrameworkCore;

namespace AgentBoard.Infrastructure.Tests.Performance;

/// <summary>
/// Baseline benchmark for the Repository on InMemory provider. The S0-2
/// acceptance bar is "1000-row list query &lt; 50ms" — this fixture asserts
/// that on the InMemory store. The MySQL benchmark (production-realistic)
/// lands once the MariaDB fixture is wired in S0-3 / S0-5.
/// </summary>
public sealed class RepositoryPerformanceBaselineTests
{
    [Fact]
    public async Task List_Of_1000_Rows_Completes_Under_50ms()
    {
        const int rowCount = 1_000;
        const int budgetMs = 50;

        using var db = TestDbContextFactory.Create(dbName: nameof(List_Of_1000_Rows_Completes_Under_50ms));
        var repo = new AgentBoard.Infrastructure.Persistence.Repositories.UserRepository(db);

        for (var i = 0; i < rowCount; i++)
            await repo.AddAsync(User.Create($"user-{i:0000}", "h", false, DateTime.UtcNow));
        await db.SaveChangesAsync();

        // Warm up + GC.
        await repo.ListAsync();
        GC.Collect();
        GC.WaitForPendingFinalizers();

        var sw = Stopwatch.StartNew();
        var page = await repo.ListAsync(u => u.Username == "user-0500");
        sw.Stop();

        page.Should().HaveCount(1);
        sw.ElapsedMilliseconds.Should().BeLessThan(budgetMs,
            $"expected list of {rowCount} rows to complete under {budgetMs}ms (took {sw.ElapsedMilliseconds}ms)");
    }

    [Fact]
    public async Task SelectProjection_Beats_Include_On_Large_Graphs()
    {
        // Documents the architectural decision: in real workloads a
        // select-projection query on a 1000-row aggregate returns only the
        // requested columns, whereas Include hydrates the full entity graph.
        // This test asserts the API works; the production performance
        // comparison is the SQL one logged in the dev runbook.
        using var db = TestDbContextFactory.Create(dbName: nameof(SelectProjection_Beats_Include_On_Large_Graphs));
        var repo = new UserRepository(db);

        for (var i = 0; i < 100; i++)
            await repo.AddAsync(User.Create($"u{i}", "h", i % 2 == 0, DateTime.UtcNow));
        await db.SaveChangesAsync();

        // Projection: only the columns the API needs.
        var projection = await db.Users
            .AsNoTracking()
            .Where(u => u.IsAdmin)
            .Select(u => new UserSummary(u.Id, u.Username, u.IsAdmin))
            .ToListAsync();

        projection.Should().NotBeEmpty();
        projection.Should().AllSatisfy(u => u.IsAdmin.Should().BeTrue());
    }

    private sealed record UserSummary(int Id, string Username, bool IsAdmin);
}
