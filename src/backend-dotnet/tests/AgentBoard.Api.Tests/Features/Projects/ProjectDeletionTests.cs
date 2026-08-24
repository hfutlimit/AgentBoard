// SPDX-License-Identifier: MIT
using System.Net;
using AgentBoard.Api.Tests.Infrastructure;
using AgentBoard.Domain.Entities;
using AgentBoard.Infrastructure.Persistence;
using FluentAssertions;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;

namespace AgentBoard.Api.Tests.Features.Projects;

public sealed class ProjectDeletionTests : IClassFixture<ApiWebApplicationFactory>
{
	private readonly ApiWebApplicationFactory _factory;

	public ProjectDeletionTests(ApiWebApplicationFactory factory) => _factory = factory;

	[Fact]
	public async Task Delete_Removes_All_DotNet_Owned_Project_Children()
	{
		int projectId;
		await using (var scope = _factory.Services.CreateAsyncScope())
		{
			var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
			var project = new Project
			{
				Name = "delete-complete",
				Description = string.Empty,
				CreatedAt = DateTime.UtcNow,
			};
			db.Projects.Add(project);
			await db.SaveChangesAsync();
			projectId = project.Id;

			var epic = new Epic
			{
				ProjectId = projectId,
				Title = "epic",
				Description = string.Empty,
				Status = "backlog",
				CreatedAt = DateTime.UtcNow,
			};
			db.Epics.Add(epic);
			await db.SaveChangesAsync();

			var story = new Story
			{
				EpicId = epic.Id,
				Title = "story",
				Description = string.Empty,
				Status = "backlog",
				CreatedAt = DateTime.UtcNow,
			};
			db.Stories.Add(story);
			await db.SaveChangesAsync();

			var task = new TaskItem
			{
				ProjectId = projectId,
				StoryId = story.Id,
				Type = "dev",
				Title = "task",
				Status = "todo",
				Priority = "medium",
				Description = string.Empty,
				Spec = string.Empty,
				Labels = "[]",
				NeededCapabilities = "[]",
				DomainTags = "[]",
				AssignmentMode = "claim",
				CreatedAt = DateTime.UtcNow,
				UpdatedAt = DateTime.UtcNow,
			};
			db.Tasks.Add(task);
			db.Sprints.Add(new Sprint { ProjectId = projectId, Title = "sprint", CreatedAt = DateTime.UtcNow });
			db.DocumentFolders.Add(new DocumentFolder
			{
				ProjectId = projectId,
				Name = "folder",
				CreatedAt = DateTime.UtcNow,
				UpdatedAt = DateTime.UtcNow,
			});
			db.WebhookConfigs.Add(new WebhookConfig
			{
				ProjectId = projectId,
				Name = "hook",
				Url = "https://example.test/hook",
				CreatedAt = DateTime.UtcNow,
				UpdatedAt = DateTime.UtcNow,
			});
			await db.SaveChangesAsync();

			db.Comments.Add(new Comment { TaskId = task.Id, Author = "a", Content = "c", CreatedAt = DateTime.UtcNow, UpdatedAt = DateTime.UtcNow });
			db.Attachments.Add(new Attachment { TaskId = task.Id, Filename = "f", OriginalName = "f", MimeType = "text/plain", CreatedAt = DateTime.UtcNow });
			db.TaskDependencies.Add(new TaskDependency { TaskId = task.Id, DependsOnId = task.Id, CreatedAt = DateTime.UtcNow });
			db.TaskStatusHistories.Add(new TaskStatusHistory { TaskId = task.Id, FromStatus = "todo", ToStatus = "done", CreatedAt = DateTime.UtcNow });
			db.StoryStatusHistories.Add(new StoryStatusHistory { StoryId = story.Id, FromStatus = "backlog", ToStatus = "done", CreatedAt = DateTime.UtcNow });
			db.Documents.Add(new Document
			{
				ProjectId = projectId,
				Title = "doc",
				Content = "content",
				CreatedAt = DateTime.UtcNow,
				UpdatedAt = DateTime.UtcNow,
			});
			await db.SaveChangesAsync();
		}

		var response = await _factory.CreateClient().DeleteAsync($"/api/projects/{projectId}");
		response.StatusCode.Should().Be(HttpStatusCode.OK);

		await using var verifyScope = _factory.Services.CreateAsyncScope();
		var verifyDb = verifyScope.ServiceProvider.GetRequiredService<AppDbContext>();
		(await verifyDb.Projects.CountAsync(p => p.Id == projectId)).Should().Be(0);
		(await verifyDb.Epics.CountAsync(e => e.ProjectId == projectId)).Should().Be(0);
		(await verifyDb.Stories.CountAsync(s => s.EpicId > 0)).Should().Be(0);
		(await verifyDb.Tasks.CountAsync(t => t.ProjectId == projectId)).Should().Be(0);
		(await verifyDb.Sprints.CountAsync(s => s.ProjectId == projectId)).Should().Be(0);
		(await verifyDb.Documents.CountAsync(d => d.ProjectId == projectId)).Should().Be(0);
		(await verifyDb.DocumentFolders.CountAsync(f => f.ProjectId == projectId)).Should().Be(0);
		(await verifyDb.WebhookConfigs.CountAsync(w => w.ProjectId == projectId)).Should().Be(0);
		(await verifyDb.Comments.CountAsync()).Should().Be(0);
		(await verifyDb.Attachments.CountAsync()).Should().Be(0);
		(await verifyDb.TaskDependencies.CountAsync()).Should().Be(0);
		(await verifyDb.TaskStatusHistories.CountAsync()).Should().Be(0);
		(await verifyDb.StoryStatusHistories.CountAsync()).Should().Be(0);
	}
}
