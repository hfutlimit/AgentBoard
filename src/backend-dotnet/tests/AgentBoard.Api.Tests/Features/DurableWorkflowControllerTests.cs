// SPDX-License-Identifier: MIT
using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using AgentBoard.Api.Durable;
using AgentBoard.Api.Features.DurableWorkflow;
using AgentBoard.Api.Tests.Infrastructure;
using AgentBoard.Application.Identity;
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
        using var client = NewAuthenticatedClient(_factory);
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
        using var client = NewAuthenticatedClient(factory);
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

    [Fact]
    public async Task Real_dependency_contract_blocks_downstream_but_not_upstream_and_unlocks_after_done()
    {
        using var factory = ApiWebApplicationFactory.CreateDurable();
        using var client = NewAuthenticatedClient(factory);
        var prerequisiteId = await SeedEligibleTask(factory.Services);
        int dependentId;
        using (var scope = factory.Services.CreateScope())
        {
            var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
            var prerequisite = await db.Tasks.FindAsync(prerequisiteId);
            var dependent = new TaskItem
            {
                ProjectId = prerequisite!.ProjectId,
                OwnerUserId = prerequisite.OwnerUserId,
                Title = "B depends on A",
                Type = "dev",
                Description = string.Empty,
                Spec = string.Empty,
                NeededCapabilities = "[]",
                CreatedAt = DateTime.UtcNow,
                UpdatedAt = DateTime.UtcNow,
            };
            db.Tasks.Add(dependent);
            await db.SaveChangesAsync();
            dependentId = dependent.Id;
            db.TaskDependencies.Add(new TaskDependency
            {
                TaskId = dependentId, DependsOnId = prerequisiteId,
                DependencyType = "blocks", CreatedAt = DateTime.UtcNow,
            });
            await db.SaveChangesAsync();
        }
        var version = DevelopmentOnlyVersion("dependency-version");
        (await client.PostAsJsonAsync("/api/durable-workflows/versions", version, Wire))
            .StatusCode.Should().Be(HttpStatusCode.OK);
        var blocked = await client.PostAsJsonAsync("/api/durable-workflows/runs",
            new StartRunRequest("dependent", version.VersionId, dependentId, "workspace", "base"), Wire);
        blocked.StatusCode.Should().Be(HttpStatusCode.Conflict);
        (await blocked.Content.ReadAsStringAsync()).Should().Contain($"blocked by tasks: {prerequisiteId}.");
        var runtime = factory.Services.GetRequiredService<DurableServerRuntime>();
        runtime.Read(plane => plane.Registry.Snapshot("dependent")).Should().BeNull();
        runtime.Read(plane => plane.Outbox.Messages).Should().BeEmpty();

        var upstream = await client.PostAsJsonAsync("/api/durable-workflows/runs",
            new StartRunRequest("prerequisite", version.VersionId, prerequisiteId, "workspace", "base"), Wire);
        upstream.StatusCode.Should().Be(HttpStatusCode.OK);
        using (var scope = factory.Services.CreateScope())
        {
            var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
            (await db.Tasks.FindAsync(prerequisiteId))!.Status = "done";
            await db.SaveChangesAsync();
        }
        var unlocked = await client.PostAsJsonAsync("/api/durable-workflows/runs",
            new StartRunRequest("dependent", version.VersionId, dependentId, "workspace", "base"), Wire);
        unlocked.StatusCode.Should().Be(HttpStatusCode.OK);
    }

    [Theory]
    [InlineData(401)]
    [InlineData(403)]
    [InlineData(404)]
    [InlineData(500)]
    [InlineData(503)]
    public async Task Dependency_read_failure_returns_503_without_mutating_the_plane(int status)
    {
        using var factory = ApiWebApplicationFactory.CreateDurable();
        factory.DependencyReadStatus = (HttpStatusCode)status;
        using var client = NewAuthenticatedClient(factory);
        var taskId = await SeedEligibleTask(factory.Services);
        var response = await client.PostAsJsonAsync("/api/durable-workflows/runs",
            new StartRunRequest("unverified", "unpublished", taskId, "workspace", "base"), Wire);
        response.StatusCode.Should().Be(HttpStatusCode.ServiceUnavailable);
        response.Headers.RetryAfter!.Delta.Should().Be(TimeSpan.FromSeconds(30));
        var runtime = factory.Services.GetRequiredService<DurableServerRuntime>();
        runtime.Read(plane => plane.Registry.Snapshot("unverified")).Should().BeNull();
        runtime.Read(plane => plane.Outbox.Messages).Should().BeEmpty();
        runtime.Read(plane => plane.TaskProjections.Entries).Should().BeEmpty();
    }

    private static HttpClient NewAuthenticatedClient(ApiWebApplicationFactory factory)
    {
        // The durable gate rejects anonymous callers (401) before the enabled
        // check, so every request here must present an identity. A locally
        // issued HMAC bearer (v1) is validated entirely inside the BFF — no
        // FastAPI round-trip — which keeps this a hermetic controller test.
        using var scope = factory.Services.CreateScope();
        var tokens = scope.ServiceProvider.GetRequiredService<ITokenService>();
        var client = factory.CreateClient();
        client.DefaultRequestHeaders.Authorization =
            new AuthenticationHeaderValue("Bearer", tokens.IssueToken(1));
        return client;
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
