// SPDX-License-Identifier: MIT
using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using AgentBoard.Api.Features.DurableWorkflow;
using AgentBoard.Api.Tests.Infrastructure;
using AgentBoard.Contracts;
using FluentAssertions;

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
    public async Task Http_surface_persists_and_exposes_a_dispatched_durable_operation()
    {
        using var client = _factory.CreateClient();
        var nodes = new[]
        {
            new WorkflowNode(
                "development", StageType.Development, "development", "{}", "{}",
                Array.Empty<StageType>(), "retry-standard", "policy-v1",
                new StageBudget(3600, 600), HandoffRequired: false),
        };
        var version = new WorkflowVersion(
            "version-http", "definition-http", 1, "workflow.v1", nodes,
            WorkflowGraph.ComputeContentHash(nodes));

        (await client.PostAsJsonAsync("/api/durable-workflows/versions", version, Wire))
            .StatusCode.Should().Be(HttpStatusCode.OK);
        (await client.PostAsJsonAsync("/api/durable-workflows/runs",
            new StartRunRequest("run-http", version.VersionId), Wire))
            .StatusCode.Should().Be(HttpStatusCode.OK);
        (await client.PostAsJsonAsync("/api/durable-workflows/runs/run-http/stages",
            new AddStageRequest("stage-http", StageType.Development, 1, null), Wire))
            .StatusCode.Should().Be(HttpStatusCode.OK);
        (await client.PostAsJsonAsync("/api/durable-workflows/stages/stage-http/executions",
            new AddExecutionRequest("execution-http"), Wire))
            .StatusCode.Should().Be(HttpStatusCode.OK);

        var assigned = await client.PostAsJsonAsync(
            "/api/durable-workflows/executions/execution-http/assign",
            new DispatchRequest("worker-http", "agent.dev", new[] { "development" },
                "policy-v1", 600, TaskContext: "implement target-v1", ProviderId: "scenario"), Wire);
        assigned.StatusCode.Should().Be(HttpStatusCode.OK);

        var operations = await client.GetStringAsync("/api/durable-workflows/operations");
        operations.Should().Contain("execution.assign");
        operations.Should().Contain("worker-http");

        var run = await client.GetAsync("/api/durable-workflows/runs/run-http");
        run.StatusCode.Should().Be(HttpStatusCode.OK);
        (await run.Content.ReadAsStringAsync()).Should().Contain("stage-http");
    }
}
