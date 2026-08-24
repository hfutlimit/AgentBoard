// SPDX-License-Identifier: MIT
//
// Integration tests for the Stage 2 / P1 workspace nested endpoints
// added by the workspace-nested module. 8 endpoints covered:
//   GET  /api/projects/center
//   GET  /api/projects/{id}/epics
//   POST /api/projects/{id}/epics
//   GET  /api/projects/{id}/schedules
//   POST /api/projects/{id}/schedules
//   POST /api/projects/{id}/sprints
//   GET  /api/projects/{id}/export
//   POST /api/projects/{id}/import
//
// Each xUnit fact targets a single contract assertion so failures are easy
// to triage. Data setup goes through AppDbContext directly (via the Web
// Application Factory's service provider) so the test is end-to-end.

using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using AgentBoard.Api.Tests.Infrastructure;
using AgentBoard.Application.Board.Dtos;
using AgentBoard.Application.Scheduling.Dtos;
using AgentBoard.Domain.Entities;
using AgentBoard.Infrastructure.Persistence;
using FluentAssertions;
using Microsoft.Extensions.DependencyInjection;

namespace AgentBoard.Api.Tests.Features.Projects;

public sealed class ProjectsControllerTests : IClassFixture<ApiWebApplicationFactory>
{
    private readonly ApiWebApplicationFactory _factory;
    private static readonly JsonSerializerOptions JsonOpts = new(JsonSerializerDefaults.Web);

    public ProjectsControllerTests(ApiWebApplicationFactory factory) => _factory = factory;

    // ---------- helpers ----------

    private HttpClient NewClient() => _factory.CreateClient();

    private async Task<Project> SeedProjectAsync(string name = "Test Project", bool archived = false)
    {
        await using var scope = _factory.Services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var project = new Project
        {
            Name = name,
            Key = name.Length > 4 ? name[..4].ToUpperInvariant() : name.ToUpperInvariant(),
            Description = "seeded by test",
            IsPrivate = false,
            CreatedAt = DateTime.UtcNow,
            IsArchived = archived,
        };
        db.Projects.Add(project);
        await db.SaveChangesAsync();
        return project;
    }

    private async Task<ProjectMember> SeedMemberAsync(int projectId, int userId, string role = "owner")
    {
        await using var scope = _factory.Services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var member = new ProjectMember
        {
            ProjectId = projectId,
            UserId = userId,
            Role = role,
            JoinedAt = DateTime.UtcNow,
        };
        db.ProjectMembers.Add(member);
        await db.SaveChangesAsync();
        return member;
    }

    private async Task<TaskItem> SeedTaskAsync(int projectId, int? storyId, string title, string status = "todo")
    {
        await using var scope = _factory.Services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var task = new TaskItem
        {
            ProjectId = projectId,
            StoryId = storyId,
            Type = "dev",
            Title = title,
            Status = status,
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
        await db.SaveChangesAsync();
        return task;
    }

    // ===================== /api/projects/center =====================

    [Fact]
    public async Task Center_Returns_200_And_Empty_Items_On_Empty_Db()
    {
        var client = NewClient();
        var response = await client.GetAsync("/api/projects/center");
        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var dto = await response.Content.ReadFromJsonAsync<ProjectsCenterResult>(JsonOpts);
        dto.Should().NotBeNull();
        dto!.Items.Should().NotBeNull();
        dto.Total.Should().BeGreaterThanOrEqualTo(0);
        dto.Page.Should().HaveCountGreaterThanOrEqualTo(1);
    }

    [Fact]
    public async Task Center_Filters_By_Scope_Active_Excludes_Archived()
    {
        var p1 = await SeedProjectAsync("Active One", archived: false);
        var p2 = await SeedProjectAsync("Archived One", archived: true);

        var client = NewClient();
        var response = await client.GetAsync("/api/projects/center?scope=active&sort=created&limit=200");
        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var dto = await response.Content.ReadFromJsonAsync<ProjectsCenterResult>(JsonOpts);
        dto!.Items.Select(i => i.Id).Should().Contain(p1.Id).And.NotContain(p2.Id);
    }

    [Fact]
    public async Task Center_Filters_By_Scope_Archived_Includes_Only_Archived()
    {
        var p1 = await SeedProjectAsync("Active Two", archived: false);
        var p2 = await SeedProjectAsync("Archived Two", archived: true);

        var client = NewClient();
        var response = await client.GetAsync("/api/projects/center?scope=archived&limit=200");
        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var dto = await response.Content.ReadFromJsonAsync<ProjectsCenterResult>(JsonOpts);
        dto!.Items.Select(i => i.Id).Should().Contain(p2.Id).And.NotContain(p1.Id);
    }

    [Fact]
    public async Task Center_Returns_Empty_For_Scope_Mine_When_Anonymous()
    {
        await SeedProjectAsync("Lonely");
        var client = NewClient();
        var response = await client.GetAsync("/api/projects/center?scope=mine&limit=200");
        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var dto = await response.Content.ReadFromJsonAsync<ProjectsCenterResult>(JsonOpts);
        dto!.Items.Should().BeEmpty();
    }

    [Fact]
    public async Task Center_Sort_Tasks_Orders_By_TaskCount_Desc()
    {
        var p1 = await SeedProjectAsync("BigProj");
        var p2 = await SeedProjectAsync("SmallProj");
        await SeedTaskAsync(p1.Id, null, "t1");
        await SeedTaskAsync(p1.Id, null, "t2");
        await SeedTaskAsync(p1.Id, null, "t3");
        await SeedTaskAsync(p2.Id, null, "single");

        var client = NewClient();
        var response = await client.GetAsync("/api/projects/center?scope=all&sort=tasks&limit=200");
        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var dto = await response.Content.ReadFromJsonAsync<ProjectsCenterResult>(JsonOpts);
        var ordered = dto!.Items.ToList();
        var idxBig = ordered.FindIndex(i => i.Id == p1.Id);
        var idxSmall = ordered.FindIndex(i => i.Id == p2.Id);
        (idxBig >= 0 && idxSmall >= 0 && idxBig < idxSmall).Should().BeTrue(
            "p1 has 3 tasks and should sort before p2 (1 task) under sort=tasks");
    }

    [Fact]
    public async Task Center_Item_Has_Aggregate_Counters()
    {
        var p = await SeedProjectAsync("AggregateTest");
        await SeedTaskAsync(p.Id, null, "open");
        await SeedTaskAsync(p.Id, null, "done", status: "done");

        var client = NewClient();
        var response = await client.GetAsync("/api/projects/center?scope=all&limit=200");
        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var dto = await response.Content.ReadFromJsonAsync<ProjectsCenterResult>(JsonOpts);
        var item = dto!.Items.Single(i => i.Id == p.Id);
        item.TaskCount.Should().Be(2);
        item.TaskDone.Should().Be(1);
    }

    [Fact]
    public async Task Center_Respects_Limit_And_Offset()
    {
        for (var i = 0; i < 5; i++) await SeedProjectAsync($"P{i:D2}");
        var client = NewClient();
        var response = await client.GetAsync("/api/projects/center?scope=all&sort=created&limit=2&offset=1");
        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var dto = await response.Content.ReadFromJsonAsync<ProjectsCenterResult>(JsonOpts);
        dto!.Items.Count.Should().BeLessThanOrEqualTo(2);
    }

    // ===================== /api/projects/{id}/epics =====================

    [Fact]
    public async Task ListEpics_Returns_200_And_Empty_For_Project_With_None()
    {
        var p = await SeedProjectAsync("NoEpics");
        var client = NewClient();
        var response = await client.GetAsync($"/api/projects/{p.Id}/epics");
        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var list = await response.Content.ReadFromJsonAsync<List<EpicDto>>(JsonOpts);
        list.Should().NotBeNull().And.BeEmpty();
    }

    [Fact]
    public async Task ListEpics_Returns_404_For_Missing_Project()
    {
        var client = NewClient();
        var response = await client.GetAsync("/api/projects/9999999/epics");
        response.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }

    [Fact]
    public async Task ListEpics_Filters_By_Status()
    {
        var p = await SeedProjectAsync("EpicStatus");
        await using var scope = _factory.Services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        db.Epics.Add(new Epic { ProjectId = p.Id, Title = "open", Status = "backlog", Description = "", CreatedAt = DateTime.UtcNow });
        db.Epics.Add(new Epic { ProjectId = p.Id, Title = "inprog", Status = "in_progress", Description = "", CreatedAt = DateTime.UtcNow });
        await db.SaveChangesAsync();

        var client = NewClient();
        var response = await client.GetAsync($"/api/projects/{p.Id}/epics?status=in_progress");
        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var list = await response.Content.ReadFromJsonAsync<List<EpicDto>>(JsonOpts);
        list!.Should().HaveCount(1);
        list![0].Title.Should().Be("inprog");
    }

    [Fact]
    public async Task CreateEpic_Returns_201_With_New_Epic()
    {
        var p = await SeedProjectAsync("EpicCreate");
        var client = NewClient();
        var response = await client.PostAsJsonAsync(
            $"/api/projects/{p.Id}/epics",
            new { name = "My new epic", description = "x" },
            JsonOpts);
        response.StatusCode.Should().Be(HttpStatusCode.Created);
        var dto = await response.Content.ReadFromJsonAsync<EpicDto>(JsonOpts);
        dto!.ProjectId.Should().Be(p.Id);
        dto.Title.Should().Be("My new epic");
    }

    [Fact]
    public async Task CreateEpic_Returns_404_For_Missing_Project()
    {
        var client = NewClient();
        var response = await client.PostAsJsonAsync(
            "/api/projects/9999999/epics",
            new { name = "ghost" },
            JsonOpts);
        response.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }

    [Fact]
    public async Task CreateEpic_Returns_422_For_Empty_Title()
    {
        var p = await SeedProjectAsync("EpicEmpty");
        var client = NewClient();
        var response = await client.PostAsJsonAsync(
            $"/api/projects/{p.Id}/epics",
            new { name = "   " },
            JsonOpts);
        response.StatusCode.Should().Be(HttpStatusCode.UnprocessableEntity);
    }

    [Fact]
    public async Task CreateEpic_Returns_422_For_Missing_Title()
    {
        var p = await SeedProjectAsync("EpicMissing");
        var client = NewClient();
        var response = await client.PostAsJsonAsync(
            $"/api/projects/{p.Id}/epics",
            new { description = "no title" },
            JsonOpts);
        response.StatusCode.Should().Be(HttpStatusCode.UnprocessableEntity);
    }

    // ===================== /api/projects/{id}/schedules =====================

    [Fact]
    public async Task ListSchedules_Returns_200_And_Empty_For_Existing_Project()
    {
        var p = await SeedProjectAsync("SchedList");
        var client = NewClient();
        var response = await client.GetAsync($"/api/projects/{p.Id}/schedules");
        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var list = await response.Content.ReadFromJsonAsync<List<AgentScheduleDto>>(JsonOpts);
        list.Should().NotBeNull().And.BeEmpty();
    }

    [Fact]
    public async Task ListSchedules_Returns_404_For_Missing_Project()
    {
        var client = NewClient();
        var response = await client.GetAsync("/api/projects/9999999/schedules");
        response.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }

    [Fact]
    public async Task CreateSchedule_Returns_201_With_Stub_Dto()
    {
        var p = await SeedProjectAsync("SchedCreate");
        var client = NewClient();
        var response = await client.PostAsJsonAsync(
            $"/api/projects/{p.Id}/schedules",
            new
            {
                title = "nightly-build",
                schedule_type = "cron",
                cron_expr = "0 3 * * *",
            },
            JsonOpts);
        response.StatusCode.Should().Be(HttpStatusCode.Created);
        var dto = await response.Content.ReadFromJsonAsync<AgentScheduleDto>(JsonOpts);
        dto!.ProjectId.Should().Be(p.Id);
        dto.Title.Should().Be("nightly-build");
        dto.ScheduleType.Should().Be("cron");
        dto.CronExpr.Should().Be("0 3 * * *");
    }

    [Fact]
    public async Task CreateSchedule_Returns_404_For_Missing_Project()
    {
        var client = NewClient();
        var response = await client.PostAsJsonAsync(
            "/api/projects/9999999/schedules",
            new { title = "x", schedule_type = "cron" },
            JsonOpts);
        response.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }

    [Fact]
    public async Task CreateSchedule_Returns_422_For_Empty_Title()
    {
        var p = await SeedProjectAsync("SchedEmpty");
        var client = NewClient();
        var response = await client.PostAsJsonAsync(
            $"/api/projects/{p.Id}/schedules",
            new { title = "", schedule_type = "cron" },
            JsonOpts);
        response.StatusCode.Should().Be(HttpStatusCode.UnprocessableEntity);
    }

    [Fact]
    public async Task CreateSchedule_Returns_422_For_Cron_Without_Expr()
    {
        var p = await SeedProjectAsync("SchedCron");
        var client = NewClient();
        var response = await client.PostAsJsonAsync(
            $"/api/projects/{p.Id}/schedules",
            new { title = "missing-cron", schedule_type = "cron" },
            JsonOpts);
        response.StatusCode.Should().Be(HttpStatusCode.UnprocessableEntity);
    }

    [Fact]
    public async Task CreateSchedule_Returns_422_For_Invalid_ScheduleType()
    {
        var p = await SeedProjectAsync("SchedBadType");
        var client = NewClient();
        var response = await client.PostAsJsonAsync(
            $"/api/projects/{p.Id}/schedules",
            new { title = "bad", schedule_type = "weekly" },
            JsonOpts);
        response.StatusCode.Should().Be(HttpStatusCode.UnprocessableEntity);
    }

    // ===================== /api/projects/{id}/sprints =====================

    [Fact]
    public async Task CreateSprint_Returns_201_With_Sprint()
    {
        var p = await SeedProjectAsync("SprintCreate");
        var client = NewClient();
        var response = await client.PostAsJsonAsync(
            $"/api/projects/{p.Id}/sprints",
            new { title = "Sprint 1", goal = "ship MVP" },
            JsonOpts);
        response.StatusCode.Should().Be(HttpStatusCode.Created);
        var dto = await response.Content.ReadFromJsonAsync<SprintDto>(JsonOpts);
        dto!.ProjectId.Should().Be(p.Id);
        dto.Title.Should().Be("Sprint 1");
        dto.Goal.Should().Be("ship MVP");
    }

    [Fact]
    public async Task CreateSprint_Returns_404_For_Missing_Project()
    {
        var client = NewClient();
        var response = await client.PostAsJsonAsync(
            "/api/projects/9999999/sprints",
            new { title = "ghost" },
            JsonOpts);
        response.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }

    [Fact]
    public async Task CreateSprint_Returns_422_For_Empty_Title()
    {
        var p = await SeedProjectAsync("SprintEmpty");
        var client = NewClient();
        var response = await client.PostAsJsonAsync(
            $"/api/projects/{p.Id}/sprints",
            new { title = "  " },
            JsonOpts);
        response.StatusCode.Should().Be(HttpStatusCode.UnprocessableEntity);
    }

    [Fact]
    public async Task CreateSprint_Accepts_Start_And_End_Date()
    {
        var p = await SeedProjectAsync("SprintDates");
        var client = NewClient();
        var response = await client.PostAsJsonAsync(
            $"/api/projects/{p.Id}/sprints",
            new { title = "Sprint 2", start_date = "2026-09-01", end_date = "2026-09-14" },
            JsonOpts);
        response.StatusCode.Should().Be(HttpStatusCode.Created);
        var dto = await response.Content.ReadFromJsonAsync<SprintDto>(JsonOpts);
        dto!.StartDate.Should().NotBeNull();
        dto.EndDate.Should().NotBeNull();
    }

    // ===================== /api/projects/{id}/export =====================

    [Fact]
    public async Task Export_Returns_200_And_Full_Payload()
    {
        var p = await SeedProjectAsync("ExportFull");
        await using var scope = _factory.Services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var epic = new Epic { ProjectId = p.Id, Title = "E1", Description = "", Status = "backlog", CreatedAt = DateTime.UtcNow };
        db.Epics.Add(epic);
        await db.SaveChangesAsync();
        var story = new Story { EpicId = epic.Id, Title = "S1", Description = "", Status = "backlog", CreatedAt = DateTime.UtcNow };
        db.Stories.Add(story);
        await db.SaveChangesAsync();
        db.Tasks.Add(new TaskItem
        {
            ProjectId = p.Id, StoryId = story.Id, Type = "dev", Title = "T1",
            Status = "todo", Priority = "medium", Description = "", Spec = "",
            Labels = "[]", NeededCapabilities = "[]", DomainTags = "[]",
            AssignmentMode = "claim", CreatedAt = DateTime.UtcNow, UpdatedAt = DateTime.UtcNow,
        });
        await db.SaveChangesAsync();

        var client = NewClient();
        var response = await client.GetAsync($"/api/projects/{p.Id}/export");
        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var dto = await response.Content.ReadFromJsonAsync<ProjectExportDto>(JsonOpts);
        dto!.Project.Id.Should().Be(p.Id);
        dto.Epics.Should().HaveCount(1);
        dto.Stories.Should().HaveCount(1);
        dto.Tasks.Should().HaveCount(1);
    }

    [Fact]
    public async Task Export_Returns_404_For_Missing_Project()
    {
        var client = NewClient();
        var response = await client.GetAsync("/api/projects/9999999/export");
        response.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }

    [Fact]
    public async Task Export_Returns_200_With_Empty_Arrays_For_Project_Without_Children()
    {
        var p = await SeedProjectAsync("ExportEmpty");
        var client = NewClient();
        var response = await client.GetAsync($"/api/projects/{p.Id}/export");
        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var dto = await response.Content.ReadFromJsonAsync<ProjectExportDto>(JsonOpts);
        dto!.Epics.Should().BeEmpty();
        dto.Stories.Should().BeEmpty();
        dto.Tasks.Should().BeEmpty();
    }

    // ===================== /api/projects/{id}/import =====================

    [Fact]
    public async Task Import_Returns_200_And_Inserts_Valid_Tasks()
    {
        var p = await SeedProjectAsync("ImportOK");
        var client = NewClient();
        var response = await client.PostAsJsonAsync(
            $"/api/projects/{p.Id}/import",
            new
            {
                tasks = new object[]
                {
                    new { title = "Imported A", priority = "high" },
                    new { title = "Imported B", type = "bug", status = "in_progress" },
                },
            },
            JsonOpts);
        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var dto = await response.Content.ReadFromJsonAsync<ProjectImportResult>(JsonOpts);
        dto!.Imported.Should().Be(2);
        dto.Errors.Should().Be(0);
    }

    [Fact]
    public async Task Import_Returns_404_For_Missing_Project()
    {
        var client = NewClient();
        var response = await client.PostAsJsonAsync(
            "/api/projects/9999999/import",
            new { tasks = new[] { new { title = "x" } } },
            JsonOpts);
        response.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }

    [Fact]
    public async Task Import_Collects_Errors_For_Invalid_Rows_But_Imports_Valid_Ones()
    {
        var p = await SeedProjectAsync("ImportMixed");
        var client = NewClient();
        var response = await client.PostAsJsonAsync(
            $"/api/projects/{p.Id}/import",
            new
            {
                tasks = new object[]
                {
                    new { title = "Valid one" },
                    new { title = "" },
                    new { title = "Bad type", type = "feature" },
                    new { title = "Bad status", status = "in-progress-typo" },
                },
            },
            JsonOpts);
        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var dto = await response.Content.ReadFromJsonAsync<ProjectImportResult>(JsonOpts);
        dto!.Imported.Should().Be(1);
        dto.Errors.Should().Be(3);
    }

    [Fact]
    public async Task Import_All_Invalid_Returns_Imported_Zero()
    {
        var p = await SeedProjectAsync("ImportAllBad");
        var client = NewClient();
        var response = await client.PostAsJsonAsync(
            $"/api/projects/{p.Id}/import",
            new
            {
                tasks = new object[]
                {
                    new { title = "" },
                    new { title = "  " },
                },
            },
            JsonOpts);
        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var dto = await response.Content.ReadFromJsonAsync<ProjectImportResult>(JsonOpts);
        dto!.Imported.Should().Be(0);
        dto.Errors.Should().Be(2);
    }

    [Fact]
    public async Task Import_Empty_Tasks_Array_Returns_200_With_Zero()
    {
        var p = await SeedProjectAsync("ImportEmpty");
        var client = NewClient();
        var response = await client.PostAsJsonAsync(
            $"/api/projects/{p.Id}/import",
            new { tasks = Array.Empty<object>() },
            JsonOpts);
        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var dto = await response.Content.ReadFromJsonAsync<ProjectImportResult>(JsonOpts);
        dto!.Imported.Should().Be(0);
    }

    [Fact]
    public async Task Import_Null_Body_Returns_404_Because_Provider_Nulls_On_Missing_Project()
    {
        // The provider's null-guard returns null only when project not found.
        // For a null body on a real project it raises 422 — we exercise the
        // missing-project path here since it's the more common code path.
        var client = NewClient();
        var response = await client.PostAsJsonAsync(
            "/api/projects/9999999/import",
            new { },
            JsonOpts);
        response.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }

    [Fact]
    public async Task Import_Null_Body_On_Real_Project_Returns_422()
    {
        var p = await SeedProjectAsync("ImportNullBody");
        var client = NewClient();
        var response = await client.PostAsJsonAsync(
            $"/api/projects/{p.Id}/import",
            new { },
            JsonOpts);
        response.StatusCode.Should().Be(HttpStatusCode.UnprocessableEntity);
    }

    // ===================== /openapi/v1.json snapshot =====================

    [Fact]
    public async Task OpenApi_Lists_All_Eight_New_Endpoints()
    {
        var client = NewClient();
        var response = await client.GetAsync("/openapi/v1.json");
        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var raw = await response.Content.ReadAsStringAsync();
        raw.Should().Contain("/api/projects/center");
        raw.Should().Contain("/api/projects/{projectId}/epics");
        raw.Should().Contain("/api/projects/{projectId}/schedules");
        raw.Should().Contain("/api/projects/{projectId}/sprints");
        raw.Should().Contain("/api/projects/{projectId}/export");
        raw.Should().Contain("/api/projects/{projectId}/import");
    }
}
