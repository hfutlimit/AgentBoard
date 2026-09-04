// SPDX-License-Identifier: MIT
using AgentBoard.Contracts;
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Workflow.Durable;

/// <summary>A capability name and the minimum level required from an Agent profile.</summary>
public sealed record AgentCapabilityRequirement(string Name, double MinimumLevel = 1);

/// <summary>
/// Durable business identity and execution context bound to a workflow run.
/// Worker and Agent identities are deliberately absent: the Server selector
/// derives them for every stage from the current AgentBoard eligibility model.
/// </summary>
public sealed record WorkflowWorkContext(
    int ProjectId,
    string WorkItemType,
    int WorkItemId,
    int OwnerUserId,
    WorkspaceReference Workspace,
    string TaskContext,
    IReadOnlyList<AgentCapabilityRequirement> RequiredCapabilities,
    string? TaskType = null);

public sealed record AgentSelectionRequest(
    string WorkflowRunId,
    string StageRunId,
    StageType StageType,
    int ProjectId,
    int OwnerUserId,
    IReadOnlyList<AgentCapabilityRequirement> RequiredCapabilities,
    IReadOnlySet<string> ExcludedAgentIds);

public sealed record AgentSelection(
    string WorkerId,
    string AgentId,
    IReadOnlyList<string> Capabilities,
    string? ProviderId = null);

/// <summary>Server-owned eligibility seam; callers never choose a Worker or Agent.</summary>
public interface IAgentSelector
{
    AgentSelection? Select(AgentSelectionRequest request);
}

internal sealed class NoAgentSelector : IAgentSelector
{
    public static NoAgentSelector Instance { get; } = new();
    public AgentSelection? Select(AgentSelectionRequest request) => null;
}

public sealed record WorkflowStagePlan(
    string StageRunId,
    string ExecutionId,
    string NodeId,
    string? HandoffId);

public sealed record WorkflowRunContextState(string RunId, WorkflowWorkContext Context);

public sealed record WorkflowOrchestrationState(
    IReadOnlyList<WorkflowRunContextState> Runs,
    IReadOnlyList<WorkflowStagePlan> StagePlans);

/// <summary>Durable metadata the graph runtime needs after a Server restart.</summary>
public sealed class WorkflowOrchestrationRegistry
{
    private readonly Dictionary<string, WorkflowWorkContext> _runs = new(StringComparer.Ordinal);
    private readonly Dictionary<string, WorkflowStagePlan> _plans = new(StringComparer.Ordinal);

    public IReadOnlyCollection<WorkflowStagePlan> StagePlans => _plans.Values;

    public bool ContainsRun(string runId) => _runs.ContainsKey(runId);

    public IReadOnlyList<string> RunIdsForWorkItem(string workItemType, int workItemId) => _runs
        .Where(entry =>
            string.Equals(entry.Value.WorkItemType, workItemType, StringComparison.OrdinalIgnoreCase)
            && entry.Value.WorkItemId == workItemId)
        .Select(entry => entry.Key)
        .ToArray();

    public void AddRun(string runId, WorkflowWorkContext context)
    {
        if (!_runs.TryAdd(runId, Freeze(context)))
        {
            throw new DuplicateException($"workflow run context '{runId}' already exists");
        }
    }

    public WorkflowWorkContext RequireRun(string runId) =>
        _runs.TryGetValue(runId, out var context)
            ? context
            : throw new NotFoundException($"workflow run context '{runId}' not found");

    public void UpdateWorkspace(string runId, WorkspaceReference workspace)
    {
        var current = RequireRun(runId);
        _runs[runId] = current with { Workspace = workspace };
    }

    public void AddPlan(WorkflowStagePlan plan)
    {
        if (!_plans.TryAdd(plan.StageRunId, plan))
        {
            throw new DuplicateException($"workflow stage plan '{plan.StageRunId}' already exists");
        }
    }

    public WorkflowStagePlan RequirePlan(string stageRunId) =>
        _plans.TryGetValue(stageRunId, out var plan)
            ? plan
            : throw new NotFoundException($"workflow stage plan '{stageRunId}' not found");

    public WorkflowOrchestrationState Capture() => new(
        _runs.Select(pair => new WorkflowRunContextState(pair.Key, pair.Value)).ToList(),
        _plans.Values.ToList());

    internal void Clear()
    {
        _runs.Clear();
        _plans.Clear();
    }

    internal void Restore(WorkflowOrchestrationState state)
    {
        foreach (var run in state.Runs) _runs[run.RunId] = Freeze(run.Context);
        foreach (var plan in state.StagePlans) _plans[plan.StageRunId] = plan;
    }

    private static WorkflowWorkContext Freeze(WorkflowWorkContext context) => context with
    {
        RequiredCapabilities = new System.Collections.ObjectModel.ReadOnlyCollection<AgentCapabilityRequirement>(
            context.RequiredCapabilities.ToArray()),
    };
}

public sealed record WorkflowStartResult(
    WorkflowRun Run,
    StageRun Stage,
    Execution Execution,
    Assignment? Assignment);

public sealed record WorkflowAdvanceResult(StageRun? Stage, Execution? Execution, Assignment? Assignment)
{
    public static WorkflowAdvanceResult Completed { get; } = new(null, null, null);
}

/// <summary>
/// The only production graph runtime. It starts the entry node, derives every
/// later stage from the published graph, creates a verified handoff, selects an
/// eligible Agent/Worker, and finishes the WorkflowRun when the graph ends.
/// </summary>
public sealed class WorkflowOrchestrator
{
    private readonly WorkflowRegistry _registry;
    private readonly WorkflowOrchestrationRegistry _state;
    private readonly LeaseRegistry _leases;
    private readonly CommandDispatcher _dispatcher;
    private readonly HandoffIssuer _handoffs;
    private readonly TaskStatusProjectionOutbox _taskProjections;
    private readonly IAgentSelector _selector;
    private readonly Func<string> _nextId;

    public WorkflowOrchestrator(
        WorkflowRegistry registry,
        WorkflowOrchestrationRegistry state,
        LeaseRegistry leases,
        CommandDispatcher dispatcher,
        HandoffIssuer handoffs,
        TaskStatusProjectionOutbox taskProjections,
        Func<string> nextId,
        IAgentSelector? selector = null)
    {
        _registry = registry;
        _state = state;
        _leases = leases;
        _dispatcher = dispatcher;
        _handoffs = handoffs;
        _taskProjections = taskProjections;
        _nextId = nextId;
        _selector = selector ?? NoAgentSelector.Instance;
    }

    public WorkflowStartResult Start(string runId, string versionId, WorkflowWorkContext context)
    {
        Validate(context);
        var conflictingRun = _state.RunIdsForWorkItem(context.WorkItemType, context.WorkItemId)
            .Select(_registry.RequireRun)
            .FirstOrDefault(existing => existing.Current.State is not (
                WorkflowRunState.Failed or WorkflowRunState.Cancelled));
        if (conflictingRun is not null)
        {
            throw new DuplicateException(
                $"work item '{context.WorkItemType}:{context.WorkItemId}' already has durable workflow run " +
                $"'{conflictingRun.Current.RunId}' in state '{conflictingRun.Current.State}'");
        }
        var version = _registry.RequireVersion(versionId);
        var entry = WorkflowGraphNavigator.EntryNode(version);
        var run = _registry.CreateRun(runId, versionId);
        _state.AddRun(runId, context);
        _registry.MoveRun(runId, WorkflowRunState.Queued, Context("workflow queued"));
        run = _registry.MoveRun(runId, WorkflowRunState.Running, Context("workflow started"));
        EnqueueTaskStatus(runId, context, "in_progress", null, "durable workflow started");

        var stage = _registry.AddStage(runId, $"stg-{_nextId()}", entry.StageType, 1, null);
        var execution = _registry.AddExecution(stage.StageRunId, $"exec-{_nextId()}");
        var plan = new WorkflowStagePlan(stage.StageRunId, execution.ExecutionId, entry.NodeId, null);
        _state.AddPlan(plan);
        var assignment = TryDispatch(plan, auditUnavailable: true);
        return new WorkflowStartResult(run, stage, execution, assignment);
    }

    public bool Manages(string runId) => _state.ContainsRun(runId);

    public WorkflowAdvanceResult Succeed(string stageRunId, string executionId)
    {
        var stage = _registry.RequireStage(stageRunId);
        ValidateAcceptedExecution(stageRunId, executionId);
        if (stage.Current.State != StageRunState.Succeeded)
        {
            throw new InvalidValueException($"stage '{stageRunId}' must be Succeeded before the graph advances");
        }

        var run = _registry.RequireRun(stage.Current.RunId);
        var version = _registry.RequireVersion(run.VersionId);
        var next = WorkflowGraphNavigator.Successor(version, stage.Current.StageType);
        if (next is null)
        {
            _registry.MoveRun(run.Current.RunId, WorkflowRunState.Succeeded,
                Context($"terminal {stage.Current.StageType} outcome accepted"));
            EnqueueTaskStatus(
                run.Current.RunId,
                _state.RequireRun(run.Current.RunId),
                "done",
                "completed",
                $"durable workflow succeeded at terminal {stage.Current.StageType} stage");
            return WorkflowAdvanceResult.Completed;
        }

        EnqueueTaskStatus(
            run.Current.RunId,
            _state.RequireRun(run.Current.RunId),
            next.StageType is StageType.Review or StageType.Qa ? "in_review" : "in_progress",
            null,
            $"durable workflow entered {next.StageType} stage");

        return CreateSuccessor(stage, executionId, next, reason: null);
    }

    public WorkflowAdvanceResult RequestChanges(string reviewStageRunId, string executionId, TransitionContext context)
    {
        var review = _registry.RequireStage(reviewStageRunId);
        ValidateAcceptedExecution(reviewStageRunId, executionId);
        var run = _registry.RequireRun(review.Current.RunId);
        var version = _registry.RequireVersion(run.VersionId);
        var next = WorkflowGraphNavigator.FeedbackSuccessor(version, review.Current.StageType);
        var maximum = version.Nodes.FirstOrDefault(node => node.StageType == StageType.Qa)?.MaxReworkIterations;
        var reworkCount = run.Stages.Count(stage => stage.Current.StageType == StageType.Development
            && stage.Current.Reason is StageRunReasons.ChangesRequested or StageRunReasons.QaChangesRequested);
        if (maximum is { } limit && reworkCount >= limit)
        {
            _registry.MoveStage(reviewStageRunId, StageRunState.ChangesRequested, context);
            Fail(reviewStageRunId, $"shared rework limit {limit} reached", "rework_limit_reached");
            return WorkflowAdvanceResult.Completed;
        }
        var stage = _registry.RequestChangesIteration(
            reviewStageRunId, $"stg-{_nextId()}", context);
        EnqueueTaskStatus(
            run.Current.RunId,
            _state.RequireRun(run.Current.RunId),
            "in_progress",
            null,
            $"durable {review.Current.StageType} requested changes");
        return CreatePlannedStage(review, executionId, next, stage);
    }

    public void Fail(string stageRunId, string reason, string statusReason = "workflow_failed")
    {
        var run = _registry.RequireRun(_registry.RequireStage(stageRunId).Current.RunId);
        if (run.Current.State == WorkflowRunState.Running)
        {
            _registry.MoveRun(run.Current.RunId, WorkflowRunState.Failed, Context(reason));
            EnqueueTaskStatus(
                run.Current.RunId,
                _state.RequireRun(run.Current.RunId),
                "blocked",
                statusReason,
                $"durable workflow failed: {reason}");
        }
    }

    public void Cancel(string stageRunId, string reason)
    {
        var run = _registry.RequireRun(_registry.RequireStage(stageRunId).Current.RunId);
        if (run.Current.State == WorkflowRunState.Running)
        {
            _registry.MoveRun(run.Current.RunId, WorkflowRunState.Cancelled, Context(reason));
            EnqueueTaskStatus(
                run.Current.RunId,
                _state.RequireRun(run.Current.RunId),
                "blocked",
                "workflow_cancelled",
                $"durable workflow cancelled: {reason}");
        }
    }

    /// <summary>Retries deferred assignment against the latest AgentBoard presence snapshot.</summary>
    public int ResumePendingAssignments()
    {
        var dispatched = 0;
        foreach (var plan in _state.StagePlans.ToList())
        {
            var stage = _registry.RequireStage(plan.StageRunId);
            var run = _registry.RequireRun(stage.Current.RunId);
            var execution = _registry.RequireExecution(plan.ExecutionId);
            if (run.Current.State != WorkflowRunState.Running
                || stage.Current.State != StageRunState.Pending
                || execution.Attempts.Count > 0)
            {
                continue;
            }

            if (TryDispatch(plan, auditUnavailable: false) is not null) dispatched++;
        }

        return dispatched;
    }

    private WorkflowAdvanceResult CreateSuccessor(
        TrackedStage source, string executionId, WorkflowNode next, string? reason)
    {
        var run = _registry.RequireRun(source.Current.RunId);
        var iteration = run.Stages
            .Where(candidate => candidate.Current.StageType == next.StageType)
            .Select(candidate => candidate.Current.Iteration)
            .DefaultIfEmpty(0)
            .Max() + 1;
        var stage = _registry.AddStage(
            run.Current.RunId, $"stg-{_nextId()}", next.StageType, iteration, reason);
        return CreatePlannedStage(source, executionId, next, stage);
    }

    private void ValidateAcceptedExecution(string stageRunId, string executionId)
    {
        var execution = _registry.RequireExecution(executionId);
        if (!string.Equals(execution.Current.StageRunId, stageRunId, StringComparison.Ordinal))
        {
            throw new InvalidValueException(
                $"execution '{executionId}' does not belong to stage '{stageRunId}'");
        }
        if (execution.Outcome is null)
        {
            throw new InvalidValueException(
                $"execution '{executionId}' has no accepted outcome to advance from");
        }
    }

    private WorkflowAdvanceResult CreatePlannedStage(
        TrackedStage source, string sourceExecutionId, WorkflowNode next, StageRun stage)
    {
        var work = _state.RequireRun(source.Current.RunId);
        var accepted = _registry.RequireExecution(sourceExecutionId).Outcome!;
        var acceptedEvidence = _handoffs.EvidenceFor(accepted.AcceptedAttemptId);
        var workspace = string.IsNullOrWhiteSpace(acceptedEvidence?.CommitOrVersion)
            ? work.Workspace
            : work.Workspace with { BaseVersion = acceptedEvidence.CommitOrVersion! };
        _state.UpdateWorkspace(source.Current.RunId, workspace);

        var handoff = _handoffs.Issue(
            source.Current.StageRunId,
            sourceExecutionId,
            next.StageType,
            RequiredCapabilities(work, next),
            workspace,
            work.TaskContext);
        var execution = _registry.AddExecution(stage.StageRunId, $"exec-{_nextId()}");
        var plan = new WorkflowStagePlan(stage.StageRunId, execution.ExecutionId, next.NodeId, handoff.HandoffId);
        _state.AddPlan(plan);
        var assignment = TryDispatch(plan, auditUnavailable: true);
        return new WorkflowAdvanceResult(stage, execution, assignment);
    }

    private Assignment? TryDispatch(WorkflowStagePlan plan, bool auditUnavailable)
    {
        var stage = _registry.RequireStage(plan.StageRunId);
        var run = _registry.RequireRun(stage.Current.RunId);
        var version = _registry.RequireVersion(run.VersionId);
        var node = version.Nodes.Single(candidate => candidate.NodeId == plan.NodeId);
        var work = _state.RequireRun(run.Current.RunId);
        var requirements = RequiredCapabilityRequirements(work, node);
        var excluded = ExcludedAgents(run, stage.Current.StageType);
        var selected = _selector.Select(new AgentSelectionRequest(
            run.Current.RunId,
            stage.Current.StageRunId,
            stage.Current.StageType,
            work.ProjectId,
            work.OwnerUserId,
            requirements,
            excluded));

        if (selected is null)
        {
            if (auditUnavailable)
            {
                _registry.Audit.Append("server", "stage.assignment.deferred", stage.Current.StageRunId,
                    "no eligible online AgentInstance for project, owner, capability, and self-review constraints");
            }
            return null;
        }

        var offered = new HashSet<string>(selected.Capabilities, StringComparer.OrdinalIgnoreCase);
        var missing = requirements.Where(required => !offered.Contains(required.Name)).Select(required => required.Name).ToList();
        if (missing.Count > 0)
        {
            throw new InvalidValueException(
                $"Agent selector returned '{selected.AgentId}' without required capabilities: {string.Join(", ", missing)}");
        }

        var assignment = _dispatcher.Dispatch(
            plan.ExecutionId,
            selected.WorkerId,
            selected.AgentId,
            selected.Capabilities,
            node.PolicyRequirements,
            node.Budget.Lease,
            plan.HandoffId,
            work.TaskContext,
            selected.ProviderId,
            work.Workspace,
            work.WorkItemType,
            work.WorkItemId,
            work.TaskType);
        _registry.Audit.Append("server", "stage.agent.selected", stage.Current.StageRunId,
            $"selected agent '{selected.AgentId}' on worker '{selected.WorkerId}'");
        return assignment;
    }

    private HashSet<string> ExcludedAgents(TrackedRun run, StageType target)
    {
        if (target is not (StageType.Review or StageType.Qa)) return new HashSet<string>(StringComparer.Ordinal);

        var excludeTypes = target == StageType.Review
            ? new HashSet<StageType> { StageType.Development }
            : new HashSet<StageType> { StageType.Development, StageType.Review };
        var stageIds = run.Stages
            .Where(stage => excludeTypes.Contains(stage.Current.StageType))
            .Select(stage => stage.Current.StageRunId)
            .ToHashSet(StringComparer.Ordinal);
        return _leases.Capture()
            .Where(assignment => stageIds.Contains(assignment.StageRunId))
            .Select(assignment => assignment.AgentId)
            .ToHashSet(StringComparer.Ordinal);
    }

    private static IReadOnlyList<string> RequiredCapabilities(WorkflowWorkContext work, WorkflowNode node) =>
        RequiredCapabilityRequirements(work, node).Select(requirement => requirement.Name).ToList();

    private static IReadOnlyList<AgentCapabilityRequirement> RequiredCapabilityRequirements(
        WorkflowWorkContext work, WorkflowNode node)
    {
        var requirements = new Dictionary<string, double>(StringComparer.OrdinalIgnoreCase);
        foreach (var required in work.RequiredCapabilities)
        {
            requirements[required.Name] = Math.Max(
                required.MinimumLevel,
                requirements.GetValueOrDefault(required.Name));
        }
        requirements[node.RequiredCapability] = Math.Max(1, requirements.GetValueOrDefault(node.RequiredCapability));
        return requirements.Select(pair => new AgentCapabilityRequirement(pair.Key, pair.Value)).ToList();
    }

    private static void Validate(WorkflowWorkContext context)
    {
        if (context.ProjectId <= 0) throw new InvalidValueException("workflow work context requires a project id");
        if (context.WorkItemId <= 0) throw new InvalidValueException("workflow work context requires a work item id");
        if (context.OwnerUserId <= 0) throw new InvalidValueException("workflow work item has no owner; assignment fails closed");
        ArgumentException.ThrowIfNullOrWhiteSpace(context.WorkItemType);
        ArgumentException.ThrowIfNullOrWhiteSpace(context.Workspace.WorkspaceId);
        ArgumentException.ThrowIfNullOrWhiteSpace(context.Workspace.BaseVersion);
        if (!string.Equals(context.Workspace.ProjectId, context.ProjectId.ToString(), StringComparison.Ordinal))
        {
            throw new InvalidValueException("workspace project identity does not match the work item project");
        }
    }

    private void EnqueueTaskStatus(
        string runId,
        WorkflowWorkContext context,
        string status,
        string? statusReason,
        string reason)
    {
        if (!string.Equals(context.WorkItemType, "task", StringComparison.OrdinalIgnoreCase)) return;
        _taskProjections.Enqueue(
            $"tsp-{_nextId()}", runId, context.WorkItemId, status, statusReason, reason);
    }

    private static TransitionContext Context(string reason) =>
        new("workflow-orchestrator", reason, SchemaVersions.Registry);
}

/// <summary>Builds truthful handoffs from the accepted outcome and its evidence.</summary>
public sealed class HandoffIssuer
{
    private readonly WorkflowRegistry _registry;
    private readonly HandoffRegistry _handoffs;
    private readonly AttemptEvidenceLog _evidence;
    private readonly Func<string> _nextId;

    public HandoffIssuer(
        WorkflowRegistry registry,
        HandoffRegistry handoffs,
        AttemptEvidenceLog evidence,
        Func<string> nextId)
    {
        _registry = registry;
        _handoffs = handoffs;
        _evidence = evidence;
        _nextId = nextId;
    }

    public AttemptEvidence? EvidenceFor(string attemptId) => _evidence.For(attemptId);

    public HandoffContext Issue(
        string sourceStageRunId,
        string executionId,
        StageType targetStageType,
        IReadOnlyList<string> requiredCapabilities,
        WorkspaceReference workspace,
        string taskContext = "{}")
    {
        var execution = _registry.RequireExecution(executionId);
        if (execution.Outcome is not { } outcome)
        {
            throw new InvalidValueException(
                $"execution '{executionId}' has no accepted outcome; a handoff must carry one (doc 151 section 7)");
        }
        if (!string.Equals(execution.Current.StageRunId, sourceStageRunId, StringComparison.Ordinal))
        {
            throw new InvalidValueException(
                $"execution '{executionId}' belongs to stage '{execution.Current.StageRunId}', not claimed source stage '{sourceStageRunId}'");
        }

        var evidence = _evidence.For(outcome.AcceptedAttemptId);
        if (!string.IsNullOrWhiteSpace(evidence?.CommitOrVersion)
            && !string.Equals(evidence.CommitOrVersion, workspace.BaseVersion, StringComparison.Ordinal))
        {
            throw new InvalidValueException(
                $"workspace base version '{workspace.BaseVersion}' does not match accepted evidence version '{evidence.CommitOrVersion}'");
        }

        var handoff = new HandoffContext
        {
            HandoffId = $"hnd-{_nextId()}",
            SourceStageRunId = sourceStageRunId,
            SourceOutcomeId = outcome.OutcomeId,
            TargetStageType = targetStageType,
            TaskContext = taskContext,
            ArtifactReferences = evidence?.ArtifactReferences ?? Array.Empty<ArtifactReference>(),
            Workspace = workspace,
            CommitOrVersion = evidence?.CommitOrVersion,
            TestEvidence = evidence?.TestEvidence ?? Array.Empty<string>(),
            ReviewFindings = evidence?.ReviewFindings ?? Array.Empty<string>(),
            ContextVersion = "handoff.v1",
            RequiredCapabilities = requiredCapabilities,
        };
        var stored = _handoffs.Add(handoff);
        _registry.Audit.Append("server", "handoff.issued", stored.HandoffId,
            $"{execution.Current.StageRunId} -> {targetStageType} over outcome '{outcome.OutcomeId}'");
        return stored;
    }
}
