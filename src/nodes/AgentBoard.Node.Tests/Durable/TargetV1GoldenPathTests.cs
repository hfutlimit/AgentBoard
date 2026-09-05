// SPDX-License-Identifier: MIT
using AgentBoard.Contracts;
using AgentBoard.Domain.Workflow;
using AgentBoard.Domain.Workflow.Durable;
using AgentBoard.Node.Agents;
using AgentBoard.Node.Durable;
using Microsoft.Extensions.Logging.Abstractions;
using Xunit;

namespace AgentBoard.Node.Tests.Durable;

/// <summary>A3 vertical slice across the real Server and Node durable components.</summary>
public sealed class TargetV1GoldenPathTests
{
    private const string Worker = "worker-golden";
    private DateTimeOffset _now = new(2026, 9, 4, 0, 0, 0, TimeSpan.Zero);
    private int _id;

    [Fact]
    public async Task Development_review_rework_review_qa_reaches_one_durable_final_path()
    {
        var policy = CompiledPolicy.Compile(PolicyPresets.Developer,
            new Dictionary<string, PolicyDecision>());
        var selector = new ScenarioSelector();
        var server = new DurableServerPlane(
            () => _now, () => (++_id).ToString("D4"), agentSelector: selector);
        var nodes = new[]
        {
            Node(policy.RevisionId, StageType.Development, StageType.Review),
            Node(policy.RevisionId, StageType.Review, StageType.Development, StageType.Qa),
            Node(policy.RevisionId, StageType.Qa),
        };
        var version = new WorkflowVersion(
            "version-1", "definition-1", 1, "workflow.v1", nodes,
            WorkflowGraph.ComputeContentHash(nodes));
        server.Registry.PublishVersion(version);

        var adapter = new ScriptedAdapter(new[]
        {
            Output("succeeded", "development complete", "commit-1", tests: new[] { "unit: green" }),
            Output("changes_requested", "review asks for changes", findings: new[] { "fix boundary" }),
            Output("succeeded", "rework complete", "commit-2", tests: new[] { "unit: green", "regression: green" }),
            Output("succeeded", "review approved"),
            Output("succeeded", "qa passed", tests: new[] { "e2e: green" }),
        });
        var journal = new InMemoryNodeCommandJournal();
        var resultTransport = new LoopbackResultTransport(server);
        var resultOutbox = new LocalResultOutbox(resultTransport, () => _now);
        var runner = new DurableAssignmentRunner(
            Worker, journal, new AssignmentTracker(), new LocalEventStore(), resultOutbox,
            new AgentAdapterRegistry(new IAgentAdapter[] { adapter }, NullLogger<AgentAdapterRegistry>.Instance),
            policy,
            new SingleLocalWorkspaceResolver(
                new WorkspaceReference("3", "workspace", "commit-0"), Directory.GetCurrentDirectory()),
            () => _now);

        var started = server.Orchestrator.Start(
            "run-1",
            version.VersionId,
            new WorkflowWorkContext(
                3,
                "task",
                42,
                7,
                new WorkspaceReference("3", "workspace", "commit-0"),
                "implement feature",
                Array.Empty<AgentCapabilityRequirement>()));
        Assert.NotNull(started.Assignment);

        var processed = new HashSet<string>(StringComparer.Ordinal);
        for (var step = 0; step < 5; step++)
        {
            await RunNext(server, runner, resultOutbox, resultTransport, processed);
        }

        var snapshot = server.Registry.Snapshot("run-1")!;
        Assert.Equal(WorkflowRunState.Succeeded, snapshot.Run.State);
        Assert.Equal(5, snapshot.Stages.Count);
        Assert.All(snapshot.Stages, stage =>
            Assert.All(stage.Executions, execution => Assert.NotNull(execution.Outcome)));
        Assert.Equal(4, server.Handoffs.Handoffs.Count);
        Assert.Empty(journal.Pending());
        Assert.Equal(5, adapter.Contexts.Count);
        Assert.All(adapter.Contexts, context => Assert.Equal("implement feature", context.Prompt));
        Assert.All(resultTransport.Results, result =>
            Assert.False(string.IsNullOrWhiteSpace(result.CausationId)));
        Assert.All(
            selector.Requests.Where(request => request.StageType == StageType.Review),
            request => Assert.Contains("agent.development", request.ExcludedAgentIds));
        var qaRequest = selector.Requests.Single(request => request.StageType == StageType.Qa);
        Assert.Contains("agent.development", qaRequest.ExcludedAgentIds);
        Assert.DoesNotContain("agent.review", qaRequest.ExcludedAgentIds);
        Assert.Equal(
            new[] { "in_progress", "in_review", "in_progress", "in_review", "in_review", "done" },
            server.TaskProjections.Entries
                .OrderBy(entry => entry.AvailableAt)
                .ThenBy(entry => entry.ProjectionId, StringComparer.Ordinal)
                .Select(entry => entry.TargetStatus));
        Assert.All(server.Sent.Commands, command =>
        {
            var payload = AssignmentTracker.ParseAssignPayload(command);
            Assert.NotNull(payload.StageType);
            Assert.False(string.IsNullOrWhiteSpace(payload.NodeId));
            Assert.NotNull(payload.Workspace);
        });
    }

    private async Task RunNext(
        DurableServerPlane server,
        DurableAssignmentRunner runner,
        LocalResultOutbox outbox,
        LoopbackResultTransport resultTransport,
        ISet<string> processed)
    {
        var command = Assert.Single(
            server.Sent.Commands,
            candidate => !processed.Contains(candidate.MessageId));
        processed.Add(command.MessageId);
        var acceptance = runner.Accept(command);
        Assert.Equal(AcceptanceKind.Accepted, acceptance.Kind);
        await runner.ExecuteAcceptedAsync(command, CancellationToken.None);
        Assert.Equal(1, outbox.Drain());
        Assert.Equal(ResultOutcomeKind.Accepted, resultTransport.LastVerdict!.Kind);
        _now = _now.AddSeconds(1);
    }

    private static WorkflowNode Node(string policyRevision, StageType stage, params StageType[] transitions) => new(
        stage.ToString().ToLowerInvariant(), stage, stage.ToString().ToLowerInvariant(),
        "{}", "{}", transitions, "retry-standard", policyRevision, new StageBudget(3600, 600), true);

    private static string Output(
        string status,
        string summary,
        string? commit = null,
        IReadOnlyList<string>? tests = null,
        IReadOnlyList<string>? findings = null) => System.Text.Json.JsonSerializer.Serialize(new
        {
            result_status = status,
            summary,
            commit_or_version = commit,
            test_evidence = tests ?? Array.Empty<string>(),
            review_findings = findings ?? Array.Empty<string>(),
        });

    private sealed class ScriptedAdapter : IAgentAdapter
    {
        private readonly Queue<string> _outputs;
        public ScriptedAdapter(IEnumerable<string> outputs) => _outputs = new Queue<string>(outputs);
        public string AgentType => "scenario";
        public List<ExecutionContext> Contexts { get; } = new();
        public Task<AgentExecutionResult> ExecuteAsync(ExecutionContext context, CancellationToken ct)
        {
            Contexts.Add(context);
            return Task.FromResult(new AgentExecutionResult(
                true, _outputs.Dequeue(), null, 0, TimeSpan.FromMilliseconds(1)));
        }
    }

    private sealed class ScenarioSelector : IAgentSelector
    {
        public List<AgentSelectionRequest> Requests { get; } = new();

        public AgentSelection? Select(AgentSelectionRequest request)
        {
            Requests.Add(request);
            var capability = request.StageType.ToString().ToLowerInvariant();
            return new AgentSelection(
                Worker,
                $"agent.{capability}",
                new[] { capability },
                "scenario");
        }
    }

    private sealed class LoopbackResultTransport : IResultTransport
    {
        private readonly DurableServerPlane _server;
        public LoopbackResultTransport(DurableServerPlane server) => _server = server;
        public ResultVerdict? LastVerdict { get; private set; }
        public List<ResultEnvelope> Results { get; } = new();
        public BrokerConfirm Publish(LocalOutboxRecord record)
        {
            Results.Add(record.Result);
            LastVerdict = _server.Results.Process(record.Result);
            return BrokerConfirm.Confirmed;
        }
    }
}
