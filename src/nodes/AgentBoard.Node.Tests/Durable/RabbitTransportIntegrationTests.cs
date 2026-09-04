// SPDX-License-Identifier: MIT
using System.Text.Json;
using AgentBoard.Contracts;
using AgentBoard.Domain.Workflow;
using AgentBoard.Domain.Workflow.Durable;
using AgentBoard.Infrastructure.Messaging;
using AgentBoard.Node.Agents;
using AgentBoard.Node.Durable;
using AgentBoard.Node.Tests.Fixtures;
using Microsoft.Extensions.Logging.Abstractions;
using RabbitMQ.Client;
using Xunit;

namespace AgentBoard.Node.Tests.Durable;

public sealed class RabbitTransportIntegrationTests
{
    [Fact]
    public async Task Server_dispatch_node_execution_and_result_acceptance_round_trip_through_rabbitmq()
    {
        var uri = Environment.GetEnvironmentVariable("AGENTBOARD_RABBITMQ_TEST_URI");
        if (string.IsNullOrWhiteSpace(uri)) return;

        var worker = $"integration-{Guid.NewGuid():N}";
        var now = DateTimeOffset.UtcNow;
        var next = 0;
        var server = new DurableServerPlane(() => now, () => (++next).ToString("D4"));
        var node = new WorkflowNode(
            "development", StageType.Development, "development", "{}", "{}",
            Array.Empty<StageType>(), "retry", "policy", new StageBudget(600, 300), false);
        var version = new WorkflowVersion(
            "version-rabbit", "definition-rabbit", 1, "workflow.v1", new[] { node },
            WorkflowGraph.ComputeContentHash(new[] { node }));
        server.Registry.PublishVersion(version);
        server.Registry.CreateRun("run-rabbit", version.VersionId);
        server.Registry.MoveRun("run-rabbit", WorkflowRunState.Queued, Context("queued"));
        server.Registry.MoveRun("run-rabbit", WorkflowRunState.Running, Context("started"));
        server.Registry.AddStage("run-rabbit", "stage-rabbit", StageType.Development, 1, null);
        server.Registry.AddExecution("stage-rabbit", "execution-rabbit");

        var policy = CompiledPolicy.Compile(PolicyPresets.Developer,
            new Dictionary<string, PolicyDecision>());
        var assignment = server.Dispatcher.Dispatch(
            "execution-rabbit", worker, "agent.dev", new[] { "development" },
            policy.RevisionId, TimeSpan.FromMinutes(5), taskContext: "rabbit end-to-end",
            providerId: "scenario");
        var commandTransport = new DurableRabbitCommandTransport(uri);
        Assert.Equal(1, new OutboxDispatcher(
            server.Outbox, commandTransport, server.Planner, server.DeadLetters, () => now)
            .DispatchDue());

        var factory = new ConnectionFactory { Uri = new Uri(uri) };
        using var connection = factory.CreateConnection();
        using var channel = connection.CreateModel();
        var commandDelivery = channel.BasicGet(
            DurableMessaging.WorkerCommandQueue(worker), autoAck: true);
        Assert.NotNull(commandDelivery);
        var command = JsonSerializer.Deserialize<CommandEnvelope>(
            commandDelivery!.Body.Span, ContractJson.Options)!;
        Assert.Equal(assignment.AssignmentId, command.AssignmentId);

        var journal = new InMemoryNodeCommandJournal();
        var resultOutbox = new LocalResultOutbox(new DurableRabbitResultTransport(uri), () => now);
        var adapter = FakeAgentAdapter.Success(
            "scenario",
            "{\"result_status\":\"succeeded\",\"summary\":\"rabbit path complete\",\"commit_or_version\":\"commit-rabbit\",\"test_evidence\":[\"rabbit:e2e\"]}");
        var runner = new DurableAssignmentRunner(
            worker, journal, new AssignmentTracker(), new LocalEventStore(), resultOutbox,
            new AgentAdapterRegistry(new IAgentAdapter[] { adapter },
                NullLogger<AgentAdapterRegistry>.Instance),
            policy, new WorkspaceReference("project", "workspace", "base"), () => now);

        Assert.Equal(AcceptanceKind.Accepted, runner.Accept(command).Kind);
        await runner.ExecuteAcceptedAsync(command, CancellationToken.None);
        Assert.Equal(1, resultOutbox.Drain());

        var resultDelivery = channel.BasicGet(DurableMessaging.ServerResultQueue, autoAck: true);
        Assert.NotNull(resultDelivery);
        var result = JsonSerializer.Deserialize<ResultEnvelope>(
            resultDelivery!.Body.Span, ContractJson.Options)!;
        Assert.Empty(EnvelopeValidator.ValidateResultFollowsCommand(command, result));
        Assert.Equal(ResultOutcomeKind.Accepted, server.Results.Process(result).Kind);
        Assert.NotNull(server.Registry.RequireExecution("execution-rabbit").Outcome);
        Assert.Equal("commit-rabbit", server.Evidence.For(result.AttemptId)!.CommitOrVersion);
        Assert.Equal(command.Traceparent, result.Traceparent);

        channel.QueueDelete(DurableMessaging.WorkerCommandQueue(worker));
    }

    private static TransitionContext Context(string reason) =>
        new("rabbit-integration", reason, SchemaVersions.Registry);
}
