// SPDX-License-Identifier: MIT
using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using AgentBoard.Application.Board.Dtos;
using AgentBoard.Application.Identity;
using AgentBoard.Api.Tests.Infrastructure;
using AgentBoard.Domain.Entities;
using AgentBoard.Domain.Identity;
using AgentBoard.Infrastructure.Persistence;
using FluentAssertions;
using Microsoft.Extensions.DependencyInjection;

namespace AgentBoard.Api.Tests.Features;

public sealed class DependenciesControllerTests : IClassFixture<ApiWebApplicationFactory>
{
    private readonly ApiWebApplicationFactory _factory;
    private static readonly JsonSerializerOptions JsonOpts = new(JsonSerializerDefaults.Web)
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        PropertyNameCaseInsensitive = true,
    };

    public DependenciesControllerTests(ApiWebApplicationFactory factory) => _factory = factory;

    private async Task<(int TaskId, int DependsOnId)> SeedTasksAsync()
    {
        await using var scope = _factory.Services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var project = new Project
        {
            Name = "Dependency contract project",
            Key = $"DEP-{Guid.NewGuid():N}"[..12],
            Description = string.Empty,
            CreatedAt = DateTime.UtcNow,
        };
        db.Projects.Add(project);
        await db.SaveChangesAsync();
        var tasks = new[]
        {
            NewTask(project.Id, "dependent task"),
            NewTask(project.Id, "dependency target"),
        };
        db.Tasks.AddRange(tasks);
        await db.SaveChangesAsync();
        return (tasks[0].Id, tasks[1].Id);
    }

    private static TaskItem NewTask(int projectId, string title) => new()
    {
        ProjectId = projectId,
        Type = "dev",
        Title = title,
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

    private HttpClient NewAdminClient()
    {
        using var scope = _factory.Services.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var user = db.Users.SingleOrDefault(u => u.Username == "dependencies-test-admin");
        if (user is null)
        {
            user = User.Create("dependencies-test-admin", "test-hash", isAdmin: true, DateTime.UtcNow);
            db.Users.Add(user);
            db.SaveChanges();
        }
        var tokenService = scope.ServiceProvider.GetRequiredService<ITokenService>();
        var client = _factory.CreateClient();
        client.DefaultRequestHeaders.Authorization =
            new AuthenticationHeaderValue("Bearer", tokenService.IssueToken(user.Id));
        return client;
    }

    [Fact]
    public async Task Delete_Returns_404_For_Unknown_Dependency()
    {
        using var client = _factory.CreateClient();
        var response = await client.DeleteAsync("/api/dependencies/999999");
        response.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }

    [Fact]
    public async Task ExistingDependency_CanBeCreatedListedAndDeleted()
    {
        var (taskId, dependsOnId) = await SeedTasksAsync();
        using var client = NewAdminClient();

        var created = await client.PostAsJsonAsync(
            $"/api/tasks/{taskId}/dependencies",
            new { depends_on_id = dependsOnId, dependency_type = "blocks" });
        created.StatusCode.Should().Be(HttpStatusCode.Created);
        var dependency = await created.Content.ReadFromJsonAsync<TaskDependencyDto>(JsonOpts);
        dependency.Should().NotBeNull();
        dependency!.TaskId.Should().Be(taskId);
        dependency.DependsOnId.Should().Be(dependsOnId);

        var list = await client.GetAsync($"/api/tasks/{taskId}/dependencies");
        list.StatusCode.Should().Be(HttpStatusCode.OK);
        var items = await list.Content.ReadFromJsonAsync<List<TaskDependencyDto>>(JsonOpts);
        items.Should().ContainSingle(item => item.Id == dependency.Id);

        var deleted = await client.DeleteAsync($"/api/dependencies/{dependency.Id}");
        deleted.StatusCode.Should().Be(HttpStatusCode.NoContent);
    }
}
