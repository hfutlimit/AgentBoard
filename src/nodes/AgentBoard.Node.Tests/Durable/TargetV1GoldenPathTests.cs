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
        var server = new DurableServerPlane(() => _now, () => (++_id).ToString("D4"));
        var nodes = new[]
        {
            Node(StageType.Development, StageType.Review),
            Node(StageType.Review, StageType.Development, StageType.Qa),
            Node(StageType.Qa),
        };
        var version = new WorkflowVersion(
            "version-1", "definition-1", 1, "workflow.v1", nodes,
            WorkflowGraph.ComputeContentHash(nodes));
        server.Registry.PublishVersion(version);
        server.Registry.CreateRun("run-1", version.VersionId);
        server.Registry.MoveRun("run-1", WorkflowRunState.Queued, Ctx("queued"));
        server.Registry.MoveRun("run-1", WorkflowRunState.Running, Ctx("started"));

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
            policy, new WorkspaceReference("project", "workspace", "commit-0"), () => _now);

        server.Registry.AddStage("run-1", "stage-dev-1", StageType.Development, 1, null);
        await RunStage(server, runner, resultOutbox, resultTransport, "stage-dev-1", "exec-dev-1",
            StageType.Development, policy.RevisionId, task: "implement feature");
        var devHandoff = server.IssueHandoff("stage-dev-1", "exec-dev-1", StageType.Review,
            new[] { "review" }, new WorkspaceReference("project", "workspace", "commit-1"),
            "review commit-1");

        server.Registry.AddStage("run-1", "stage-review-1", StageType.Review, 1, null);
        await RunStage(server, runner, resultOutbox, resultTransport, "stage-review-1", "exec-review-1",
            StageType.Review, policy.RevisionId, devHandoff.HandoffId);
        var iteration = Assert.IsType<StageRun>(resultTransport.LastVerdict!.CreatedIteration);
        Assert.Equal(StageRunReasons.ChangesRequested, iteration.Reason);
        var reviewHandoff = server.IssueHandoff("stage-review-1", "exec-review-1", StageType.Development,
            new[] { "development" }, new WorkspaceReference("project", "workspace", "commit-1"),
            "apply review findings");

        await RunStage(server, runner, resultOutbox, resultTransport, iteration.StageRunId, "exec-dev-2",
            StageType.Development, policy.RevisionId, reviewHandoff.HandoffId);
        var reworkHandoff = server.IssueHandoff(iteration.StageRunId, "exec-dev-2", StageType.Review,
            new[] { "review" }, new WorkspaceReference("project", "workspace", "commit-2"),
            "review commit-2");

        server.Registry.AddStage("run-1", "stage-review-2", StageType.Review, 2, null);
        await RunStage(server, runner, resultOutbox, resultTransport, "stage-review-2", "exec-review-2",
            StageType.Review, policy.RevisionId, reworkHandoff.HandoffId);
        var qaHandoff = server.IssueHandoff("stage-review-2", "exec-review-2", StageType.Qa,
            new[] { "qa" }, new WorkspaceReference("project", "workspace", "commit-2"),
            "verify commit-2");

        server.Registry.AddStage("run-1", "stage-qa-1", StageType.Qa, 1, null);
        await RunStage(server, runner, resultOutbox, resultTransport, "stage-qa-1", "exec-qa-1",
            StageType.Qa, policy.RevisionId, qaHandoff.HandoffId);
        server.Registry.MoveRun("run-1", WorkflowRunState.Succeeded, Ctx("qa outcome accepted"));

        var snapshot = server.Registry.Snapshot("run-1")!;
        Assert.Equal(WorkflowRunState.Succeeded, snapshot.Run.State);
        Assert.Equal(5, snapshot.Stages.Count);
        Assert.All(snapshot.Stages, stage =>
            Assert.All(stage.Executions, execution => Assert.NotNull(execution.Outcome)));
        Assert.Equal(4, server.Handoffs.Handoffs.Count);
        Assert.Empty(journal.Pending());
        Assert.Equal(5, adapter.Contexts.Count);
        Assert.Equal("review commit-1", adapter.Contexts[1].Prompt);
        Assert.Equal("apply review findings", adapter.Contexts[2].Prompt);
        Assert.All(resultTransport.Results, result =>
            Assert.False(string.IsNullOrWhiteSpace(result.CausationId)));
    }

    private async Task RunStage(
        DurableServerPlane server,
        DurableAssignmentRunner runner,
        LocalResultOutbox outbox,
        LoopbackResultTransport resultTransport,
        string stageId,
        string executionId,
        StageType stage,
        string policyRevision,
        string? handoffId = null,
        string task = "{}")
    {
        server.Registry.AddExecution(stageId, executionId);
        var assignment = server.Dispatcher.Dispatch(
            executionId, Worker, $"agent.{stage.ToString().ToLowerInvariant()}",
            new[] { stage.ToString().ToLowerInvariant() }, policyRevision,
            TimeSpan.FromMinutes(10), handoffId, task, providerId: "scenario");
        Assert.True(server.Sent.TryGet(assignment.AssignmentId, out var command));
        var acceptance = runner.Accept(command);
        Assert.Equal(AcceptanceKind.Accepted, acceptance.Kind);
        await runner.ExecuteAcceptedAsync(command, CancellationToken.None);
        Assert.Equal(1, outbox.Drain());
        Assert.Equal(ResultOutcomeKind.Accepted, resultTransport.LastVerdict!.Kind);
        _now = _now.AddSeconds(1);
    }

    private static WorkflowNode Node(StageType stage, params StageType[] transitions) => new(
        stage.ToString().ToLowerInvariant(), stage, stage.ToString().ToLowerInvariant(),
        "{}", "{}", transitions, "retry-standard", "policy", new StageBudget(3600, 600), true);

    private static TransitionContext Ctx(string reason) =>
        new("golden-test", reason, SchemaVersions.Registry);

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
