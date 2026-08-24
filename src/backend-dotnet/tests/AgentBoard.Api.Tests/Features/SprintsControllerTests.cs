// SPDX-License-Identifier: MIT
using System.Net;
using System.Net.Http.Json;
using AgentBoard.Api.Tests.Infrastructure;
using AgentBoard.Domain.Entities;
using AgentBoard.Infrastructure.Persistence;
using FluentAssertions;
using Microsoft.Extensions.DependencyInjection;

namespace AgentBoard.Api.Tests.Features;

/// <summary>
/// Contract tests for the P5 sprint extensions:
///   <c>GET /api/sprints/{id}/burndown</c> and <c>GET /api/sprints/{id}/tasks</c>.
/// Mirrors the FastAPI <c>/api/sprints/{sid}/burndown</c> and
/// <c>/api/sprints/{sid}/tasks</c> shapes so the BFF is interchangeable.
/// </summary>
public sealed class SprintsControllerTests : IClassFixture<ApiWebApplicationFactory>
{
    private readonly ApiWebApplicationFactory _factory;

    public SprintsControllerTests(ApiWebApplicationFactory factory) => _factory = factory;

    private async Task<int> SeedProjectAsync(string name)
    {
        using var scope = _factory.Services.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var now = DateTime.UtcNow;
        var p = new Project { Name = name, Key = name.ToLowerInvariant(), CreatedAt = now };
        db.Projects.Add(p);
        await db.SaveChangesAsync();
        return p.Id;
    }

    private async Task<int> SeedSprintAsync(int projectId, DateTime? start, DateTime? end, string status = "active")
    {
        using var scope = _factory.Services.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var now = DateTime.UtcNow;
        var s = new Sprint
        {
            ProjectId = projectId,
            Title = "Sprint " + Guid.NewGuid().ToString("N")[..6],
            Goal = "ship it",
            Status = status,
            StartDate = start,
            EndDate = end,
            CreatedAt = now,
        };
        db.Sprints.Add(s);
        await db.SaveChangesAsync();
        return s.Id;
    }

    private async Task SeedTaskAsync(int projectId, int? sprintId, string status, DateTime updatedAt)
    {
        using var scope = _factory.Services.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var t = new TaskItem
        {
            ProjectId = projectId,
            SprintId = sprintId,
            StoryId = null,
            Type = "dev",
            Title = "T-" + Guid.NewGuid().ToString("N")[..6],
            Status = status,
            Priority = "medium",
            Description = "",
            Spec = "",
            CreatedAt = updatedAt,
            UpdatedAt = updatedAt,
        };
        db.Tasks.Add(t);
        await db.SaveChangesAsync();
    }

    [Fact]
    public async Task Burndown_Returns_404_For_Unknown_Sprint()
    {
        using var client = _factory.CreateClient();
        var response = await client.GetAsync("/api/sprints/999999/burndown");
        response.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }

    [Fact]
    public async Task Burndown_Returns_200_And_Defaults_To_14_Days_When_Start_End_Missing()
    {
        var projectId = await SeedProjectAsync("burndown-default-" + Guid.NewGuid().ToString("N")[..8]);
        var sprintId = await SeedSprintAsync(projectId, start: null, end: null);
        await SeedTaskAsync(projectId, sprintId, "todo", DateTime.UtcNow);
        await SeedTaskAsync(projectId, sprintId, "done", DateTime.UtcNow);

        using var client = _factory.CreateClient();
        var response = await client.GetAsync($"/api/sprints/{sprintId}/burndown");
        response.StatusCode.Should().Be(HttpStatusCode.OK);

        var dto = await response.Content.ReadFromJsonAsync<BurndownResponse>();
        dto.Should().NotBeNull();
        dto!.sprint_id.Should().Be(sprintId);
        dto.project_id.Should().Be(projectId);
        dto.total_tasks.Should().Be(2);
        dto.done_tasks.Should().Be(1);
        dto.remaining_tasks.Should().Be(1);
        dto.current_burn_rate.Should().Be(0.5);
        dto.days.Should().HaveCount(14);
        // ideal at day 0 = total_tasks = 2
        dto.days[0].ideal_remaining.Should().Be(2);
        // actual at last day <= total_tasks
        dto.days[^1].actual_remaining.Should().BeGreaterThanOrEqualTo(0);
    }

    [Fact]
    public async Task Burndown_Honors_Days_Query_Param()
    {
        var projectId = await SeedProjectAsync("burndown-days-" + Guid.NewGuid().ToString("N")[..8]);
        var sprintId = await SeedSprintAsync(projectId, start: null, end: null);

        using var client = _factory.CreateClient();
        var response = await client.GetAsync($"/api/sprints/{sprintId}/burndown?days=7");
        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var dto = await response.Content.ReadFromJsonAsync<BurndownResponse>();
        dto.Should().NotBeNull();
        dto!.days.Should().HaveCount(7);
    }

    [Fact]
    public async Task Burndown_Computes_Ideal_As_Linear_Decline()
    {
        var projectId = await SeedProjectAsync("burndown-linear-" + Guid.NewGuid().ToString("N")[..8]);
        // 4-day sprint (Aug 1..Aug 5 = 4 day diff, 5 days inclusive), 10 tasks
        var start = new DateTime(2026, 8, 1, 0, 0, 0, DateTimeKind.Utc);
        var end = new DateTime(2026, 8, 5, 0, 0, 0, DateTimeKind.Utc);
        var sprintId = await SeedSprintAsync(projectId, start, end);
        for (var i = 0; i < 10; i++)
            await SeedTaskAsync(projectId, sprintId, "todo", DateTime.UtcNow);

        using var client = _factory.CreateClient();
        var response = await client.GetAsync($"/api/sprints/{sprintId}/burndown");
        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var dto = await response.Content.ReadFromJsonAsync<BurndownResponse>();
        dto.Should().NotBeNull();
        dto!.days.Should().HaveCount(5); // day 0..4 (5 days inclusive)
        // day 0: ideal = 10 * (1 - 0/4) = 10
        dto.days[0].ideal_remaining.Should().Be(10);
        // day 4: ideal = 10 * (1 - 4/4) = 0
        dto.days[^1].ideal_remaining.Should().Be(0);
    }

    [Fact]
    public async Task ListTasks_Returns_404_For_Unknown_Sprint()
    {
        using var client = _factory.CreateClient();
        var response = await client.GetAsync("/api/sprints/999999/tasks");
        response.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }

    [Fact]
    public async Task ListTasks_Returns_200_And_Only_Sprints_Tasks()
    {
        var projectId = await SeedProjectAsync("tasks-scope-" + Guid.NewGuid().ToString("N")[..8]);
        var sprintId = await SeedSprintAsync(projectId, null, null);
        await SeedTaskAsync(projectId, sprintId, "todo", DateTime.UtcNow);
        await SeedTaskAsync(projectId, sprintId, "in_progress", DateTime.UtcNow);
        await SeedTaskAsync(projectId, null, "done", DateTime.UtcNow); // not in sprint

        using var client = _factory.CreateClient();
        var response = await client.GetAsync($"/api/sprints/{sprintId}/tasks");
        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var dto = await response.Content.ReadFromJsonAsync<PagedResponse>();
        dto.Should().NotBeNull();
        dto!.total.Should().Be(2);
        dto.items.Should().HaveCount(2);
        dto.items.All(t => t.status != "done").Should().BeTrue();
    }

    [Fact]
    public async Task ListTasks_Filters_By_Status_Query_Param()
    {
        var projectId = await SeedProjectAsync("tasks-filter-" + Guid.NewGuid().ToString("N")[..8]);
        var sprintId = await SeedSprintAsync(projectId, null, null);
        await SeedTaskAsync(projectId, sprintId, "todo", DateTime.UtcNow);
        await SeedTaskAsync(projectId, sprintId, "todo", DateTime.UtcNow);
        await SeedTaskAsync(projectId, sprintId, "done", DateTime.UtcNow);

        using var client = _factory.CreateClient();
        var response = await client.GetAsync($"/api/sprints/{sprintId}/tasks?status=done");
        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var dto = await response.Content.ReadFromJsonAsync<PagedResponse>();
        dto.Should().NotBeNull();
        dto!.total.Should().Be(1);
        dto.items.Should().ContainSingle(t => t.status == "done");
    }

    [Fact]
    public async Task ListTasks_Honors_Limit_And_Offset()
    {
        var projectId = await SeedProjectAsync("tasks-page-" + Guid.NewGuid().ToString("N")[..8]);
        var sprintId = await SeedSprintAsync(projectId, null, null);
        for (var i = 0; i < 5; i++)
            await SeedTaskAsync(projectId, sprintId, "todo", DateTime.UtcNow);

        using var client = _factory.CreateClient();
        var first = await client.GetAsync($"/api/sprints/{sprintId}/tasks?limit=2&offset=0");
        first.StatusCode.Should().Be(HttpStatusCode.OK);
        var firstDto = await first.Content.ReadFromJsonAsync<PagedResponse>();
        firstDto!.total.Should().Be(5);
        firstDto.items.Should().HaveCount(2);

        var second = await client.GetAsync($"/api/sprints/{sprintId}/tasks?limit=2&offset=4");
        var secondDto = await second.Content.ReadFromJsonAsync<PagedResponse>();
        secondDto!.total.Should().Be(5);
        secondDto.items.Should().HaveCount(1);
    }

    private sealed record BurndownResponse(
        int sprint_id,
        int project_id,
        string title,
        string status,
        DateTime? start_date,
        DateTime? end_date,
        int total_tasks,
        int done_tasks,
        int remaining_tasks,
        double current_burn_rate,
        IReadOnlyList<BurndownDay> days);

    private sealed record BurndownDay(string date, int ideal_remaining, int actual_remaining);

    private sealed record PagedResponse(IReadOnlyList<TaskRow> items, int page, int pageSize, long total);

    private sealed record TaskRow(
        int id,
        int project_id,
        int? story_id,
        string type,
        string title,
        string status,
        string priority,
        string description,
        DateTime created_at,
        DateTime updated_at);
}
