// SPDX-License-Identifier: MIT
using AgentBoard.Contracts;
using AgentBoard.Domain.Workflow;
using AgentBoard.Domain.Workflow.Durable;

namespace AgentBoard.Domain.Tests.Durable;

/// <summary>
/// Shared fixture for the A1 durable tests: a golden-path workflow version
/// (development -&gt; review -&gt; qa), a running run, and a dispatched
/// development assignment with a live lease. Time and ids are deterministic.
/// </summary>
internal sealed class PlaneFixture
{
    public const string Trace = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01";

    public PlaneFixture()
    {
        Now = new DateTimeOffset(2026, 9, 4, 0, 0, 0, TimeSpan.Zero);
        Plane = new DurableServerPlane(() => Now, NextId);

        var nodes = new[]
        {
            Node(StageType.Development, StageType.Review),
            Node(StageType.Review, StageType.Development, StageType.Qa),
            Node(StageType.Qa),
        };
        Version = new WorkflowVersion(
            "version-golden", "definition-golden", 1, "workflow.v1",
            nodes, WorkflowGraph.ComputeContentHash(nodes));

        Plane.Registry.PublishVersion(Version);
        Plane.Registry.CreateRun("run-1", Version.VersionId);
        Plane.Registry.MoveRun("run-1", WorkflowRunState.Queued, Ctx("queued"));
        Plane.Registry.MoveRun("run-1", WorkflowRunState.Running, Ctx("started"));

        StageId = "stg-dev-1";
        ExecutionId = "exec-dev-1";
        Plane.Registry.AddStage("run-1", StageId, StageType.Development, 1, null);
        Plane.Registry.AddExecution(StageId, ExecutionId);
    }

    public DateTimeOffset Now { get; private set; }
    public DurableServerPlane Plane { get; }
    public WorkflowVersion Version { get; }
    public string StageId { get; private set; }
    public string ExecutionId { get; private set; }

    public int Counter;

    public string NextId() => (++Counter).ToString("D4");

    public void Advance(int minutes) => Now = Now.AddMinutes(minutes);

    public static WorkflowNode Node(StageType stage, params StageType[] transitions) => new(
        NodeId: stage.ToString().ToLowerInvariant(),
        StageType: stage,
        RequiredCapability: stage.ToString().ToLowerInvariant(),
        InputContract: "{}",
        OutputContract: "{}",
        AllowedTransitions: transitions,
        RetryPolicyRef: "retry-standard",
        PolicyRequirements: "policy-golden",
        Budget: new StageBudget(3600, 600),
        HandoffRequired: true);

    public static TransitionContext Ctx(string reason) =>
        new("test-harness", reason, SchemaVersions.Registry);

    /// <summary>Dispatches the first assignment for the fixture's execution.</summary>
    public Assignment DispatchDev(string worker = "worker-1", string agent = "agent.dev")
    {
        var assignment = Plane.Dispatcher.Dispatch(
            ExecutionId, worker, agent, new[] { "development" }, "policy-rev-1", TimeSpan.FromMinutes(10),
            workspace: new WorkspaceReference("p", "w", "v"));
        CurrentAssignment = assignment;
        StageId = Plane.Registry.RequireExecution(ExecutionId).Current.StageRunId;
        return assignment;
    }

    public Assignment? CurrentAssignment { get; set; }

    /// <summary>Builds a valid result envelope against the current assignment.</summary>
    public ResultEnvelope Result(
        AttemptResultStatus status,
        FailureCategory failure = FailureCategory.None,
        string? summary = null,
        string? messageId = null,
        string? idempotencyKey = null,
        long? leaseEpoch = null,
        string? assignmentId = null)
    {
        var assignment = CurrentAssignment
            ?? throw new InvalidOperationException("dispatch an assignment first");

        return new ResultEnvelope
        {
            MessageId = messageId ?? $"msg-{NextId()}",
            SchemaVersion = "result.v1",
            MessageType = MessageTypes.ExecutionResult,
            CorrelationId = assignment.WorkflowRunId,
            // A real Node answers the exact command it received; the Server
            // now enforces that causal binding, so the fixture must honor it.
            CausationId = Plane.Sent.TryGet(assignment.AssignmentId, out var issued)
                ? issued.MessageId : "cmd-unknown",
            IdempotencyKey = idempotencyKey ?? $"{assignment.AssignmentId}:{assignment.AttemptId}",
            WorkflowRunId = assignment.WorkflowRunId,
            StageRunId = assignment.StageRunId,
            ExecutionId = assignment.ExecutionId,
            AttemptId = assignment.AttemptId,
            AssignmentId = assignmentId ?? assignment.AssignmentId,
            WorkerId = assignment.WorkerId,
            AgentId = assignment.AgentId,
            LeaseEpoch = leaseEpoch ?? assignment.LeaseEpoch,
            ResultStatus = status,
            FailureCategory = failure,
            OutcomeSummary = summary,
            Traceparent = Trace,
            CreatedAt = Now,
        };
    }

    /// <summary>Runs the whole golden path up to a succeeded development stage.</summary>
    public void CompleteDevelopment()
    {
        DispatchDev();
        var verdict = Plane.Results.Process(Result(AttemptResultStatus.Succeeded, summary: "dev done"));
        if (verdict.Kind != ResultOutcomeKind.Accepted)
        {
            throw new InvalidOperationException($"fixture setup failed: {verdict.Reason}");
        }
    }

    /// <summary>Dispatches a review stage/execution and assigns it.</summary>
    public Assignment DispatchReview(string reviewStageId = "stg-rev-1")
    {
        var sourceStageId = StageId;
        var sourceExecutionId = ExecutionId;
        var handoff = Plane.IssueHandoff(
            sourceStageId,
            sourceExecutionId,
            StageType.Review,
            new[] { "review" },
            new WorkspaceReference("p", "w", "v"));
        Plane.Registry.AddStage("run-1", reviewStageId, StageType.Review, 1, null);
        var execId = $"exec-{reviewStageId}";
        Plane.Registry.AddExecution(reviewStageId, execId);
        var assignment = Plane.Dispatcher.Dispatch(
            execId, "worker-2", "agent.rev", new[] { "review" }, "policy-rev-1",
            TimeSpan.FromMinutes(10), handoff.HandoffId);
        CurrentAssignment = assignment;
        StageId = reviewStageId;
        ExecutionId = execId;
        return assignment;
    }
}
