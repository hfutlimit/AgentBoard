// SPDX-License-Identifier: MIT
using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using AgentBoard.Api.Durable;
using AgentBoard.Api.Features.DurableWorkflow;
using AgentBoard.Api.Tests.Infrastructure;
using AgentBoard.Contracts;
using AgentBoard.Domain.Entities;
using AgentBoard.Domain.Identity;
using AgentBoard.Infrastructure.Persistence;
using FluentAssertions;
using Microsoft.Extensions.DependencyInjection;

namespace AgentBoard.Api.Tests.Features;

public sealed class DurableWorkflowControllerTests : IClassFixture<ApiWebApplicationFactory>
{
    private static readonly JsonSerializerOptions Wire = new(JsonSerializerDefaults.Web)
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
    };

    private readonly ApiWebApplicationFactory _factory;

    public DurableWorkflowControllerTests(ApiWebApplicationFactory factory) => _factory = factory;

    [Fact]
    public async Task Disabled_surface_rejects_before_mutating_the_durable_plane()
    {
        using var client = _factory.CreateClient();
        var version = DevelopmentOnlyVersion("version-disabled");

        var response = await client.PostAsJsonAsync("/api/durable-workflows/versions", version, Wire);

        response.StatusCode.Should().Be(HttpStatusCode.ServiceUnavailable);
        response.Headers.RetryAfter.Should().NotBeNull();
        var runtime = _factory.Services.GetRequiredService<DurableServerRuntime>();
        runtime.Read(plane => plane.Registry.Versions).Should().BeEmpty();
        runtime.Read(plane => plane.Outbox.Messages).Should().BeEmpty();
    }

    [Fact]
    public async Task Enabled_start_selects_an_owned_online_agent_and_hides_manual_graph_mutation()
    {
        using var factory = ApiWebApplicationFactory.CreateDurable();
        using var client = factory.CreateClient();
        var taskId = await SeedEligibleTask(factory.Services);
        var version = DevelopmentOnlyVersion("version-http");

        (await client.PostAsJsonAsync("/api/durable-workflows/versions", version, Wire))
            .StatusCode.Should().Be(HttpStatusCode.OK);
        var started = await client.PostAsJsonAsync("/api/durable-workflows/runs",
            new StartRunRequest(
                "run-http", version.VersionId, taskId, "workspace-http", "commit-0",
                "implement target-v1"), Wire);
        started.StatusCode.Should().Be(HttpStatusCode.OK);

        var removedMutationResponses = new[]
        {
            await client.PostAsJsonAsync(
                "/api/durable-workflows/runs/run-http/stages",
                new { stage_run_id = "stage-http", stage_type = "development", iteration = 2 }, Wire),
            await client.PostAsJsonAsync(
                "/api/durable-workflows/stages/stage-http/executions",
                new { execution_id = "execution-http" }, Wire),
            await client.PostAsJsonAsync(
                "/api/durable-workflows/executions/execution-http/assign",
                new { worker_id = "attacker", agent_id = "attacker" }, Wire),
            await client.PostAsJsonAsync(
                "/api/durable-workflows/handoffs",
                new { source_stage_run_id = "stage-http" }, Wire),
        };
        removedMutationResponses.Should().OnlyContain(
            response => response.StatusCode == HttpStatusCode.NotFound);

        var operations = await client.GetStringAsync("/api/durable-workflows/operations");
        operations.Should().Contain("execution.assign");
        operations.Should().Contain("worker-http");
        operations.Should().Contain("agent.dev");

        var run = await client.GetAsync("/api/durable-workflows/runs/run-http");
        run.StatusCode.Should().Be(HttpStatusCode.OK);
        var body = await run.Content.ReadAsStringAsync();
        using var snapshot = JsonDocument.Parse(body);
        var stage = snapshot.RootElement.GetProperty("stages")[0].GetProperty("stage");
        stage.GetProperty("stage_type").GetInt32().Should().Be((int)StageType.Development);
        stage.GetProperty("state").GetInt32().Should().Be((int)StageRunState.Running);
    }

    private static WorkflowVersion DevelopmentOnlyVersion(string versionId)
    {
        var nodes = new[]
        {
            new WorkflowNode(
                "development", StageType.Development, "development", "{}", "{}",
                Array.Empty<StageType>(), "retry-standard", "policy-v1",
                new StageBudget(3600, 600), HandoffRequired: false),
        };
        return new WorkflowVersion(
            versionId, "definition-http", 1, "workflow.v1", nodes,
            WorkflowGraph.ComputeContentHash(nodes));
    }

    private static async Task<int> SeedEligibleTask(IServiceProvider services)
    {
        using var scope = services.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var now = DateTime.UtcNow;
        var user = User.Create("durable-owner", "hash", false, now);
        var project = new Project
        {
            Name = "Durable API",
            Key = "DUR",
            Description = string.Empty,
            CreatedAt = now,
        };
        db.Users.Add(user);
        db.Projects.Add(project);
        await db.SaveChangesAsync();

        var task = new TaskItem
        {
            ProjectId = project.Id,
            Type = "dev",
            Title = "Target v1",
            Description = "Implement durable workflow",
            Spec = string.Empty,
            OwnerUserId = user.Id,
            NeededCapabilities = "[]",
            CreatedAt = now,
            UpdatedAt = now,
        };
        db.ProjectMembers.Add(new ProjectMember
        {
            ProjectId = project.Id,
            UserId = user.Id,
            Role = "owner",
            JoinedAt = now,
        });
        db.Tasks.Add(task);
        db.Agents.Add(new Agent
        {
            AgentId = "agent.dev",
            Name = "Development Agent",
            Capabilities = "[{\"name\":\"development\",\"level\":3}]",
            UserId = user.Id,
            Online = true,
            Enabled = true,
            LastHeartbeat = now,
            CreatedAt = now,
            UpdatedAt = now,
        });
        db.Workers.Add(new Worker
        {
            WorkerId = "worker-http",
            Hostname = "test-host",
            Status = "active",
            LastHeartbeat = now,
            CreatedAt = now,
            UpdatedAt = now,
        });
        db.AgentInstances.Add(new AgentInstance
        {
            WorkerId = "worker-http",
            AgentId = "agent.dev",
            ExecutorType = "scenario",
            Enabled = true,
            Online = true,
            LastHeartbeat = now,
            CreatedAt = now,
            UpdatedAt = now,
        });
        db.WorkerProjectMappings.Add(new WorkerProjectMapping
        {
            WorkerId = "worker-http",
            ProjectId = project.Id,
            Enabled = true,
            CreatedAt = now,
        });
        await db.SaveChangesAsync();
        return task.Id;
    }
}
