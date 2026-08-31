using System.Net;
using System.Text.Json;
using AgentBoard.ProposalWorker.Agents;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Xunit;

namespace AgentBoard.ProposalWorker.Tests;

public sealed class GoldenScenarioAdapterTests
{
    [Theory]
    [InlineData(WorkloadTypes.Task, 41, "/api/tasks/41/submit-review")]
    [InlineData(WorkloadTypes.Rework, 42, "/api/tasks/42/submit-review")]
    [InlineData(WorkloadTypes.Review, 43, "/api/tasks/43/review")]
    [InlineData(WorkloadTypes.Ticket, 44, "/api/ticket-requests/88/execute")]
    public async Task Uses_production_http_contract_for_each_workload(
        string workloadType, long workloadId, string expectedPath)
    {
        var stub = new StubHandler();
        using var client = new HttpClient(stub);
        var adapter = new DeterministicScenarioAdapter(
            new FixedHttpFactory(client),
            Options.Create(new AgentBoardOptions
            {
                ServerUrl = "http://agentboard.local",
                StartupToken = "scenario-token",
            }),
            ScenarioOptions(),
            NullLogger<DeterministicScenarioAdapter>.Instance);
        var context = new ExecutionContext(
            1,
            $"golden:{workloadType}:{workloadId}",
            workloadType,
            workloadId,
            0,
            "scenario",
            "{\"ref_id\":88}",
            null);

        var result = await adapter.ExecuteAsync(context, CancellationToken.None);

        Assert.True(result.Success, result.ErrorMessage);
        var request = Assert.Single(stub.Requests);
        Assert.Equal("POST", request.Method);
        Assert.EndsWith(expectedPath, request.Url, StringComparison.Ordinal);
        Assert.Equal("Bearer", request.Headers.Authorization?.Scheme);
        Assert.Equal("scenario-token", request.Headers.Authorization?.Parameter);
        using var audit = JsonDocument.Parse(result.OutputJson!);
        Assert.Equal(workloadType,
            audit.RootElement.GetProperty("workload_type").GetString());
        Assert.Equal(200, audit.RootElement.GetProperty("status").GetInt32());
        if (workloadType == WorkloadTypes.Review)
        {
            using var body = JsonDocument.Parse(request.Body);
            Assert.Equal("approve",
                body.RootElement.GetProperty("verdict").GetString());
        }
    }

    [Fact]
    public async Task Fails_closed_when_http_contract_rejects_action()
    {
        var stub = new StubHandler
        {
            OnSend = _ => new HttpResponseMessage(HttpStatusCode.UnprocessableEntity)
            {
                Content = new StringContent("not assigned"),
            },
        };
        using var client = new HttpClient(stub);
        var adapter = new DeterministicScenarioAdapter(
            new FixedHttpFactory(client),
            Options.Create(new AgentBoardOptions
            {
                ServerUrl = "http://agentboard.local",
                StartupToken = "scenario-token",
            }),
            ScenarioOptions(),
            NullLogger<DeterministicScenarioAdapter>.Instance);

        var result = await adapter.ExecuteAsync(new ExecutionContext(
            1, "golden:task:7", WorkloadTypes.Task, 7, 0, "scenario",
            "{}", null), CancellationToken.None);

        Assert.False(result.Success);
        Assert.Equal(422, result.ExitCode);
        Assert.Contains("not assigned", result.ErrorMessage);
    }

    [Fact]
    public async Task Is_disabled_by_default_even_if_a_message_is_received()
    {
        var stub = new StubHandler();
        using var client = new HttpClient(stub);
        var adapter = new DeterministicScenarioAdapter(
            new FixedHttpFactory(client),
            Options.Create(new AgentBoardOptions
            {
                ServerUrl = "http://agentboard.local",
                StartupToken = "scenario-token",
            }),
            Options.Create(new AgentsOptions()),
            NullLogger<DeterministicScenarioAdapter>.Instance);

        var result = await adapter.ExecuteAsync(new ExecutionContext(
            1, "golden:task:7", WorkloadTypes.Task, 7, 0, "scenario",
            "{}", null), CancellationToken.None);

        Assert.False(result.Success);
        Assert.Empty(stub.Requests);
        Assert.Contains("disabled", result.ErrorMessage);
    }

    private static IOptions<AgentsOptions> ScenarioOptions() =>
        Options.Create(new AgentsOptions
        {
            Scenario = new AgentOptions { Command = "enabled" },
        });
}
