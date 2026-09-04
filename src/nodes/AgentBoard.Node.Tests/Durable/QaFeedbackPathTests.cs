using System.Text.Json;
using AgentBoard.Contracts;
using AgentBoard.Domain.Workflow.Durable;
using AgentBoard.Node.Agents;
using AgentBoard.Node.Durable;
using AgentBoard.Node.Process;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Xunit;

namespace AgentBoard.Node.Tests.Durable;

public sealed class QaFeedbackPathTests
{
    [Fact]
    public async Task Qa_defect_reopens_development_with_evidence_then_review_and_qa_complete()
    {
        var path = new PathFixture();
        await path.Step("succeeded"); // development
        await path.Step("succeeded"); // review
        var rejected = await path.Step("changes_requested"); // QA
        var rework = path.Server.Registry.RequireRun("run").Stages.Last().Current;
        Assert.Equal(StageType.Development, rework.StageType);
        Assert.Equal(2, rework.Iteration);
        Assert.Equal(StageRunReasons.QaChangesRequested, rework.Reason);
        Assert.Equal("in_progress", path.Server.TaskProjections.Entries.Last().TargetStatus);
        Assert.Equal(ResultOutcomeKind.Duplicate, path.Server.Results.Process(rejected).Kind);
        Assert.Equal(4, path.Server.Registry.RequireRun("run").Stages.Count);

        path.RestartServer();
        await path.Step("succeeded"); // rework
        Assert.Contains("reproduce boundary defect", path.Process.Prompts.Last());
        Assert.Contains("boundary-check failed", path.Process.Prompts.Last());
        Assert.Contains("artifact://qa-report", path.Process.Prompts.Last());
        Assert.Contains("commit-verified", path.Process.Prompts.Last());
        await path.Step("succeeded"); // review again
        await path.Step("succeeded"); // QA again

        Assert.Equal(WorkflowRunState.Succeeded, path.Server.Registry.RequireRun("run").Current.State);
        Assert.Equal("done", path.Server.TaskProjections.Entries.Last().TargetStatus);
        Assert.Equal(6, path.Server.Registry.RequireRun("run").Stages.Count);
        Assert.Contains("Durable qa stage", path.Process.Prompts[2]);
        Assert.Contains("Do not modify the implementation", path.Process.Prompts[2]);
        Assert.DoesNotContain("Implement the requested change", path.Process.Prompts[2]);
        Assert.Contains("task_type=dev", path.Process.Prompts[2]);
        Assert.Equal("reproduce boundary defect", Assert.Single(path.Server.Evidence.For(rejected.AttemptId)!.ReviewFindings));
        Assert.All(path.Selector.Requests.Where(r => r.StageType == StageType.Qa),
            request => Assert.Contains("agent.Development", request.ExcludedAgentIds));
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public async Task Mixed_review_and_qa_feedback_share_a_durable_budget_and_monotonic_development_iterations(bool stopAtQa)
    {
        var path = new PathFixture(maximum: 2);
        await path.Step("succeeded");
        await path.Step("changes_requested"); // review -> dev #2
        await path.Step("succeeded");
        await path.Step("succeeded");
        await path.Step("changes_requested"); // QA #1 -> dev #3, not #2
        var dev = path.Server.Registry.RequireRun("run").Stages
            .Where(s => s.Current.StageType == StageType.Development).Select(s => s.Current.Iteration);
        Assert.Equal(new[] { 1, 2, 3 }, dev);
        path.RestartServer();
        await path.Step("succeeded");
        if (stopAtQa) await path.Step("succeeded");
        await path.Step("changes_requested"); // review must not bypass the shared budget
        Assert.Equal(WorkflowRunState.Failed, path.Server.Registry.RequireRun("run").Current.State);
        var projection = path.Server.TaskProjections.Entries.Last();
        Assert.Equal("blocked", projection.TargetStatus);
        Assert.Equal("rework_limit_reached", projection.StatusReason);
        Assert.Equal(stopAtQa ? 8 : 7, path.Server.Sent.Commands.Count);
    }

    [Fact]
    public async Task Qa_process_failure_retries_the_attempt_without_reopening_development()
    {
        var path = new PathFixture();
        await path.Step("succeeded");
        await path.Step("succeeded");
        await path.Step("failed");
        Assert.Equal(3, path.Server.Registry.RequireRun("run").Stages.Count);
        Assert.Equal(WorkflowRunState.Running, path.Server.Registry.RequireRun("run").Current.State);
        Assert.Single(path.Server.Capture().PendingRetries);
    }

    [Fact]
    public async Task Qa_feedback_requires_evidence_before_mutating_the_attempt()
    {
        var path = new PathFixture();
        await path.Step("succeeded");
        await path.Step("succeeded");
        var result = await path.Execute("changes_requested");
        var verdict = path.Server.Results.Process(result with { TestEvidence = Array.Empty<string>() });
        Assert.Equal(ResultOutcomeKind.RejectedSchema, verdict.Kind);
        Assert.Null(path.Server.Registry.RequireAttempt(result.AttemptId).Result);
        Assert.Equal(3, path.Server.Registry.RequireRun("run").Stages.Count);
    }

    [Fact]
    public async Task Legacy_graph_preserves_qa_evidence_and_blocks_instead_of_silently_adding_a_feedback_edge()
    {
        var path = new PathFixture(maximum: null);
        await path.Step("succeeded");
        await path.Step("succeeded");
        var result = await path.Step("changes_requested");
        Assert.Equal(WorkflowRunState.Failed, path.Server.Registry.RequireRun("run").Current.State);
        Assert.Equal("blocked", path.Server.TaskProjections.Entries.Last().TargetStatus);
        Assert.Equal(3, path.Server.Sent.Commands.Count);
        Assert.NotNull(path.Server.Evidence.For(result.AttemptId));
    }

    private sealed class PathFixture
    {
        private DateTimeOffset _now = DateTimeOffset.UtcNow;
        private int _id;
        private readonly HashSet<string> _processed = new();
        private readonly LocalResultOutbox _outbox;
        private readonly DurableAssignmentRunner _runner;
        public DurableServerPlane Server { get; private set; }
        public RecordingProcess Process { get; } = new();
        public Selector Selector { get; } = new();

        public PathFixture(int? maximum = 3)
        {
            var policy = CompiledPolicy.Compile(PolicyPresets.Developer, new Dictionary<string, PolicyDecision>());
            Server = new DurableServerPlane(() => _now, () => (++_id).ToString(), agentSelector: Selector);
            WorkflowNode Node(StageType stage, params StageType[] next) => new(
                stage.ToString(), stage, stage.ToString(), "{}", "{}", next, "retry", policy.RevisionId, new StageBudget(600, 600), true);
            var nodes = new[]
            {
                Node(StageType.Development, StageType.Review),
                Node(StageType.Review, StageType.Development, StageType.Qa),
                Node(StageType.Qa, maximum is null ? Array.Empty<StageType>() : new[] { StageType.Development })
                    with { MaxReworkIterations = maximum },
            };
            var version = new WorkflowVersion("version", "definition", 1,
                maximum is null ? "workflow.v1" : "workflow.v1.1", nodes, WorkflowGraph.ComputeContentHash(nodes));
            Server.Registry.PublishVersion(version);
            var adapter = new CodexAdapter(Process,
                Options.Create(new AgentsOptions { Codex = new AgentOptions { Command = Environment.ProcessPath! } }),
                Options.Create(new AgentBoardOptions()), NullLogger<CodexAdapter>.Instance);
            _outbox = new LocalResultOutbox(new HoldingTransport(), () => _now);
            _runner = new DurableAssignmentRunner("worker", new InMemoryNodeCommandJournal(), new AssignmentTracker(),
                new LocalEventStore(), _outbox,
                new AgentAdapterRegistry(new[] { adapter }, NullLogger<AgentAdapterRegistry>.Instance), policy,
                new ConfiguredLocalWorkspaceResolver(Options.Create(new DurableExecutionOptions
                {
                    Workspaces = new[] { new LocalWorkspaceMappingOptions
                        { ProjectId = "3", WorkspaceId = "repo", LocalPath = Directory.GetCurrentDirectory() } },
                })), () => _now);
            Server.Orchestrator.Start("run", version.VersionId, new WorkflowWorkContext(3, "task", 42, 7,
                new WorkspaceReference("3", "repo", "base"), "implement feature", Array.Empty<AgentCapabilityRequirement>(), "dev"));
        }

        public async Task<ResultEnvelope> Execute(string status)
        {
            var command = Assert.Single(Server.Sent.Commands, candidate => !_processed.Contains(candidate.MessageId));
            _processed.Add(command.MessageId);
            Process.Output = JsonSerializer.Serialize(new
            {
                result_status = status, summary = "checked {boundary}", commit_or_version = "commit-verified",
                test_evidence = new[] { status == "changes_requested" ? "boundary-check failed" : "verification passed" },
                review_findings = status == "changes_requested" ? new[] { "reproduce boundary defect" } : Array.Empty<string>(),
                artifact_references = new[] { new ArtifactReference("artifact://qa-report", new string('a', 64), 12) },
            });
            Assert.Equal(AcceptanceKind.Accepted, _runner.Accept(command).Kind);
            await _runner.ExecuteAcceptedAsync(command, CancellationToken.None);
            _now = _now.AddSeconds(1);
            return _outbox.Records.Single(row => row.Result.CausationId == command.MessageId).Result;
        }

        public async Task<ResultEnvelope> Step(string status)
        {
            var result = await Execute(status);
            var verdict = Server.Results.Process(result);
            Assert.True(verdict.Kind == ResultOutcomeKind.Accepted, verdict.Reason);
            return result;
        }

        public void RestartServer()
        {
            // Serialize the state, like SQLite, rather than reusing live objects.
            var snapshot = JsonSerializer.Deserialize<PlaneState>(JsonSerializer.Serialize(Server.Capture()))!;
            Server = DurableServerPlane.Restore(() => _now, () => (++_id).ToString(), snapshot, Selector);
        }
    }

    private sealed class RecordingProcess : IProcessExecutor
    {
        public string Output { get; set; } = "{}";
        public List<string> Prompts { get; } = new();
        public Task<ProcessResult> ExecuteAsync(ProcessSpec spec, CancellationToken ct)
        {
            Prompts.Add(spec.StdinPayload!);
            var jsonl = JsonSerializer.Serialize(new { type = "item.completed", item = new { type = "agent_message", text = Output } });
            return Task.FromResult(new ProcessResult { ExitCode = 0, RedactedOutput = jsonl });
        }
    }

    private sealed class Selector : IAgentSelector
    {
        public List<AgentSelectionRequest> Requests { get; } = new();
        public AgentSelection? Select(AgentSelectionRequest request)
        {
            Requests.Add(request);
            return new AgentSelection("worker", $"agent.{request.StageType}", new[] { request.StageType.ToString() }, "codex");
        }
    }

    private sealed class HoldingTransport : IResultTransport
    {
        public BrokerConfirm Publish(LocalOutboxRecord record) => BrokerConfirm.Confirmed;
    }
}
