// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Entities;
using AgentBoard.Application.Abstractions;
using FluentAssertions;
using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using AgentBoard.Infrastructure.Persistence;

namespace AgentBoard.Infrastructure.Tests.Persistence;

public sealed class UnitOfWorkTransactionTests
{
	[Fact]
	public async Task Rollback_Removes_Changes_Made_Inside_Transaction()
	{
		await using var connection = new SqliteConnection("Data Source=:memory:");
		await connection.OpenAsync();
		var options = new DbContextOptionsBuilder<AppDbContext>()
			.UseSqlite(connection)
			.Options;

		await using var db = new AppDbContext(options);
		await db.Database.EnsureCreatedAsync();

		var unitOfWork = (IUnitOfWork)db;
		await using (var transaction = await unitOfWork.BeginTransactionAsync())
		{
			db.Projects.Add(new Project
			{
				Name = "rolled back",
				Description = string.Empty,
				CreatedAt = DateTime.UtcNow,
			});
			await db.SaveChangesAsync();
			await transaction.RollbackAsync();
		}

		db.ChangeTracker.Clear();
		(await db.Projects.CountAsync()).Should().Be(0);
	}
}
