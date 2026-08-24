// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Entities;
using AgentBoard.Domain.Identity;
using AgentBoard.Infrastructure.Persistence.Repositories;
using FluentAssertions;

namespace AgentBoard.Infrastructure.Tests.Performance;

public sealed class ReadQueryTests
{
	[Fact]
	public async Task Project_Read_Queries_Return_Paged_Joined_And_Aggregated_Results()
	{
		using var db = TestDbContextFactory.Create(dbName: nameof(Project_Read_Queries_Return_Paged_Joined_And_Aggregated_Results));
		var user = User.Create("reader", "hash", false, DateTime.UtcNow);
		var project = new Project { Name = "read project", Description = string.Empty, CreatedAt = DateTime.UtcNow };
		db.Users.Add(user);
		db.Projects.Add(project);
		await db.SaveChangesAsync();
		db.ProjectMembers.AddRange(
			new ProjectMember { ProjectId = project.Id, UserId = user.Id, Role = "owner", JoinedAt = DateTime.UtcNow },
			new ProjectMember { ProjectId = project.Id, UserId = user.Id, Role = "member", JoinedAt = DateTime.UtcNow.AddMinutes(-1) });
		db.Notifications.AddRange(
			new Notification { UserId = user.Id, Title = "unread", Content = "", Type = "task", CreatedAt = DateTime.UtcNow, IsRead = false },
			new Notification { UserId = user.Id, Title = "read", Content = "", Type = "task", CreatedAt = DateTime.UtcNow.AddMinutes(-1), IsRead = true });
		db.Tasks.AddRange(
			new TaskItem { ProjectId = project.Id, Type = "dev", Title = "done", Status = "done", Priority = "medium", Description = "", CreatedAt = DateTime.UtcNow, UpdatedAt = DateTime.UtcNow },
			new TaskItem { ProjectId = project.Id, Type = "dev", Title = "todo", Status = "todo", Priority = "medium", Description = "", CreatedAt = DateTime.UtcNow, UpdatedAt = DateTime.UtcNow });
		await db.SaveChangesAsync();

		var repository = new ProjectReadRepository(db);
		var members = await repository.ListMembersAsync(project.Id, limit: 1, offset: 0);
		var notifications = await repository.ListNotificationsAsync(user.Id, limit: 1, offset: 0, unreadOnly: true);
		var overview = await repository.GetOverviewAsync(new[] { project.Id });
		var center = await repository.GetCenterAsync(new[] { project.Id }, true, "all", "tasks", 10, 0);

		members.Total.Should().Be(2);
		members.Items.Should().ContainSingle();
		notifications.Total.Should().Be(1);
		notifications.Items.Should().ContainSingle().Which.Title.Should().Be("unread");
		overview.Counts.Tasks.Should().Be(2);
		overview.Counts.DoneTasks.Should().Be(1);
		overview.Projects.Single().Percent.Should().Be(50);
		center.Items.Should().ContainSingle().Which.TaskCount.Should().Be(2);
		center.Items.Single().TaskDone.Should().Be(1);
	}
}
