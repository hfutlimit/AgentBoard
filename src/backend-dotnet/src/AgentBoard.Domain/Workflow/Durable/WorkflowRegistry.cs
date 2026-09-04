// SPDX-License-Identifier: MIT
using AgentBoard.Contracts;
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Workflow.Durable;

/// <summary>Registry view of one attempt: its record, its machine, its result.</summary>
public sealed class TrackedAttempt
{
    internal TrackedAttempt(ExecutionAttempt attempt)
    {
        Current = attempt;
        Machine = new ExecutionAttemptStateMachine(attempt.State);
    }

    public ExecutionAttempt Current { get; internal set; }
    public ExecutionAttemptStateMachine Machine { get; }
    public AttemptResult? Result { get; internal set; }
}

/// <summary>Registry view of one execution and its attempts/outcome.</summary>
public sealed class TrackedExecution
{
    private readonly List<TrackedAttempt> _attempts = new();

    internal TrackedExecution(Execution execution) => Current = execution;

    public Execution Current { get; }
    public Outcome? Outcome { get; internal set; }
    public IReadOnlyList<TrackedAttempt> Attempts => _attempts;
    public TrackedAttempt? LatestAttempt => _attempts.Count > 0 ? _attempts[^1] : null;

    internal void Add(TrackedAttempt attempt) => _attempts.Add(attempt);
}

/// <summary>Registry view of one logical stage within a run.</summary>
public sealed class TrackedStage
{
    private readonly List<TrackedExecution> _executions = new();

    internal TrackedStage(StageRun stage)
    {
        Current = stage;
        Machine = new StageRunStateMachine(stage.State);
    }

    public StageRun Current { get; internal set; }
    public StageRunStateMachine Machine { get; }
    public IReadOnlyList<TrackedExecution> Executions => _executions;
    public TrackedExecution? ExecutionById(string executionId) =>
        _executions.FirstOrDefault(e => string.Equals(e.Current.ExecutionId, executionId, StringComparison.Ordinal));

    internal void Add(TrackedExecution execution) => _executions.Add(execution);
}

/// <summary>Registry view of one workflow run.</summary>
public sealed class TrackedRun
{
    private readonly List<TrackedStage> _stages = new();

    internal TrackedRun(WorkflowRun run, string versionId)
    {
        Current = run;
        VersionId = versionId;
        Machine = new WorkflowRunStateMachine(run.State);
    }

    public WorkflowRun Current { get; internal set; }
    public string VersionId { get; }
    public WorkflowRunStateMachine Machine { get; }
    public IReadOnlyList<TrackedStage> Stages => _stages;
    public TrackedStage? StageById(string stageId) =>
        _stages.FirstOrDefault(s => string.Equals(s.Current.StageRunId, stageId, StringComparison.Ordinal));
    public TrackedStage? ActiveStage => _stages.LastOrDefault(s =>
        !RunTransitions.IsTerminal(s.Current.State));

    internal void Add(TrackedStage stage) => _stages.Add(stage);
}

/// <summary>
/// The authoritative workflow/run registry (doc 151 §1: "Server 是 workflow 和
/// logical outcome 的权威来源"; doc 150 PR-001, PR-002).
/// </summary>
/// <remarks>
/// <para>
/// Every state change funnels through the entity's own
/// <see cref="RunStateMachine{TState}"/>, so an illegal move throws before any
/// bookkeeping happens, and every accepted move is written to the audit trail
/// with its <see cref="TransitionContext"/>. There is no API that rebinds a run
/// to another version or edits a published version: doc 151 §4.2 invariant 1
/// and §12 invariant 1 are enforced by absence — you cannot call what does not
/// exist.
/// </para>
/// <para>
/// This is the in-memory half of A1; the durable half replays or rehydrates
/// these structures from the SQLite store in Infrastructure. Machines rebuilt
/// after a restart resume from the persisted state with an empty in-process
/// history, while the audit trail keeps the full sequence.
/// </para>
/// </remarks>
public sealed partial class WorkflowRegistry
{
    private readonly Dictionary<string, WorkflowVersion> _versions = new(StringComparer.Ordinal);
    private readonly Dictionary<string, TrackedRun> _runs = new(StringComparer.Ordinal);
    private readonly Dictionary<string, TrackedStage> _stages = new(StringComparer.Ordinal);
    private readonly Dictionary<string, TrackedExecution> _executions = new(StringComparer.Ordinal);
    private readonly Dictionary<string, TrackedAttempt> _attempts = new(StringComparer.Ordinal);
    private readonly Func<DateTimeOffset> _clock;

    public WorkflowRegistry(Func<DateTimeOffset> clock, AuditTrail? audit = null)
    {
        _clock = clock ?? throw new ArgumentNullException(nameof(clock));
        Audit = audit ?? new AuditTrail(clock);
    }

    public AuditTrail Audit { get; }

    public IReadOnlyCollection<WorkflowVersion> Versions => _versions.Values;
    public IReadOnlyCollection<TrackedRun> Runs => _runs.Values;

    internal void Clear()
    {
        _versions.Clear();
        _runs.Clear();
        _stages.Clear();
        _executions.Clear();
        _attempts.Clear();
        Audit.Clear();
    }

    // ---------------------------------------------------------------------
    // Versions (doc 150 PR-001)
    // ---------------------------------------------------------------------

    public WorkflowVersion PublishVersion(WorkflowVersion version)
    {
        var errors = WorkflowValidator.Validate(version);
        if (errors.Count > 0)
        {
            throw new InvalidValueException(
                $"invalid workflow version: {string.Join("; ", errors.Select(e => $"{e.Field} {e.Reason}"))}");
        }

        // Records freeze membership, not contents: a caller still holds the
        // List<WorkflowNode> it passed in. Recomputing the canonical hash
        // against the caller's collections, then storing defensive copies,
        // closes both the silent-mutation window and the trusting-the-string
        // window (doc 151 §4.1, §12 invariant 1).
        var expectedHash = WorkflowGraph.ComputeContentHash(version.Nodes);
        if (!string.Equals(version.ContentHash, expectedHash, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidValueException(
                $"content hash '{version.ContentHash}' does not match the graph ({expectedHash}); " +
                "published versions must prove their nodes, not merely name a hash");
        }

        var frozen = WorkflowGraph.Freeze(version);

        if (!_versions.TryAdd(frozen.VersionId, frozen))
        {
            throw new DuplicateException(
                $"workflow version '{frozen.VersionId}' is already published; versions are immutable");
        }

        Audit.Append("server", "workflow.version.published", frozen.VersionId,
            $"published v{frozen.Version} of definition '{frozen.DefinitionId}'");
        return frozen;
    }

    public WorkflowVersion RequireVersion(string versionId) =>
        _versions.TryGetValue(versionId, out var version)
            ? version
            : throw new NotFoundException($"workflow version '{versionId}' not found");

    // ---------------------------------------------------------------------
    // Runs
    // ---------------------------------------------------------------------

    public WorkflowRun CreateRun(string runId, string versionId)
    {
        var version = RequireVersion(versionId);

        if (!_runs.TryAdd(runId, new TrackedRun(
                new WorkflowRun(runId, version.VersionId, WorkflowRunState.Draft, _clock()), versionId)))
        {
            throw new DuplicateException($"run '{runId}' already exists");
        }

        return _runs[runId].Current;
    }

    public WorkflowRun MoveRun(string runId, WorkflowRunState to, TransitionContext ctx)
    {
        var run = RequireRun(runId);
        var transition = run.Machine.MoveTo(to, ctx);
        run.Current = run.Current with { State = run.Machine.Current };
        Audit.RecordTransition("run", runId, transition);
        return run.Current;
    }

    public TrackedRun RequireRun(string runId) =>
        _runs.TryGetValue(runId, out var run)
            ? run
            : throw new NotFoundException($"run '{runId}' not found");

    // ---------------------------------------------------------------------
    // Stages
    // ---------------------------------------------------------------------

    /// <summary>
    /// Appends a stage to a run. Iteration 1 of a stage type must exist in the
    /// bound workflow version (fail-closed for unknown node types, doc 150
    /// PR-001); later iterations of the same type are the sanctioned way
    /// review feedback continues the run (doc 151 §4.2 invariant 2).
    /// </summary>
    public StageRun AddStage(string runId, string stageId, StageType stageType, int iteration, string? reason)
    {
        var run = RequireRun(runId);

        if (run.Machine.IsTerminal)
        {
            throw new InvalidValueException($"run '{runId}' is {run.Current.State}; stages cannot be added to a terminal run");
        }

        if (iteration < 1)
        {
            throw new InvalidValueException("stage iterations start at 1");
        }

        if (iteration == 1 && RequireVersion(run.VersionId).Nodes.All(n => n.StageType != stageType))
        {
            throw new InvalidValueException(
                $"stage type '{stageType}' is not declared by workflow version '{run.VersionId}'");
        }

        if (run.Stages.Any(s => s.Current.StageType == stageType && s.Current.Iteration == iteration))
        {
            throw new DuplicateException(
                $"run '{runId}' already has a {stageType} stage at iteration {iteration}");
        }

        var stage = new StageRun(stageId, runId, stageType, iteration, reason, StageRunState.Pending);

        if (_stages.TryAdd(stageId, new TrackedStage(stage)))
        {
            run.Add(_stages[stageId]);
        }
        else
        {
            throw new DuplicateException($"stage '{stageId}' already exists");
        }

        Audit.Append("server", "stage.added", stageId,
            $"{stageType} iteration {iteration}{(reason is null ? "" : $" ({reason})")}");
        return stage;
    }

    public StageRun MoveStage(string stageId, StageRunState to, TransitionContext ctx)
    {
        var stage = RequireStage(stageId);
        var transition = stage.Machine.MoveTo(to, ctx);
        stage.Current = stage.Current with { State = stage.Machine.Current };
        Audit.RecordTransition("stage", stageId, transition);
        return stage.Current;
    }

    /// <summary>
    /// Ends a changes-requested review and creates the follow-up development
    /// iteration through the A0-frozen succession rules, never a fix stage.
    /// </summary>
    public StageRun RequestChangesIteration(string reviewStageId, string nextStageId, TransitionContext ctx)
    {
        var review = RequireStage(reviewStageId);

        if (review.Current.StageType != StageType.Review)
        {
            throw new InvalidValueException($"stage '{reviewStageId}' is not a review stage");
        }

        MoveStage(reviewStageId, StageRunState.ChangesRequested, ctx);
        var next = WorkflowValidator.NextDevelopmentIteration(review.Current, nextStageId);

        var run = RequireRun(review.Current.RunId);
        if (!_stages.TryAdd(nextStageId, new TrackedStage(next)))
        {
            throw new DuplicateException($"stage '{nextStageId}' already exists");
        }

        run.Add(_stages[nextStageId]);
        Audit.Append("server", "stage.iteration.created", nextStageId,
            $"development iteration {next.Iteration} after changes requested on '{reviewStageId}'");
        return next;
    }

    public TrackedStage RequireStage(string stageId) =>
        _stages.TryGetValue(stageId, out var stage)
            ? stage
            : throw new NotFoundException($"stage '{stageId}' not found");

    // ---------------------------------------------------------------------
    // Executions and attempts
    // ---------------------------------------------------------------------

    public Execution AddExecution(string stageId, string executionId)
    {
        var stage = RequireStage(stageId);

        if (RunTransitions.IsTerminal(stage.Current.State))
        {
            throw new InvalidValueException($"stage '{stageId}' is {stage.Current.State}; executions cannot be added");
        }

        var execution = new Execution(executionId, stageId);

        if (!_executions.TryAdd(executionId, new TrackedExecution(execution)))
        {
            throw new DuplicateException($"execution '{executionId}' already exists");
        }

        stage.Add(_executions[executionId]);
        return execution;
    }

    public TrackedExecution RequireExecution(string executionId) =>
        _executions.TryGetValue(executionId, out var execution)
            ? execution
            : throw new NotFoundException($"execution '{executionId}' not found");

    public ExecutionAttempt AddAttempt(string executionId, string attemptId, long leaseEpoch)
    {
        var execution = RequireExecution(executionId);

        if (execution.Outcome is not null)
        {
            throw new InvalidValueException(
                $"execution '{executionId}' already has an accepted outcome; new attempts would duplicate it (doc 151 §4.2 invariant 4)");
        }

        var attempt = new ExecutionAttempt(attemptId, executionId, leaseEpoch, ExecutionAttemptState.Created);

        if (!_attempts.TryAdd(attemptId, new TrackedAttempt(attempt)))
        {
            throw new DuplicateException($"attempt '{attemptId}' already exists");
        }

        execution.Add(_attempts[attemptId]);

        // A stage starts working when its first attempt starts.
        var stage = RequireStage(execution.Current.StageRunId);
        if (stage.Machine.Current == StageRunState.Assigned)
        {
            MoveStage(stage.Current.StageRunId, StageRunState.Running,
                new TransitionContext("server", "first attempt created", SchemaVersions.Registry));
        }

        return attempt;
    }

    public TrackedAttempt RequireAttempt(string attemptId) =>
        _attempts.TryGetValue(attemptId, out var attempt)
            ? attempt
            : throw new NotFoundException($"attempt '{attemptId}' not found");

    public ExecutionAttempt MoveAttempt(string attemptId, ExecutionAttemptState to, TransitionContext ctx)
    {
        var attempt = RequireAttempt(attemptId);
        var transition = attempt.Machine.MoveTo(to, ctx);
        attempt.Current = attempt.Current with { State = attempt.Machine.Current };
        Audit.RecordTransition("attempt", attemptId, transition);
        return attempt.Current;
    }

    /// <summary>
    /// Attaches the physical result of one attempt. Bounded by
    /// <see cref="PayloadLimits.MaxOutcomeSummaryBytes"/> because detailed
    /// output stays on the Node (doc 150 NFR-008).
    /// </summary>
    public AttemptResult RecordAttemptResult(string attemptId, AttemptResult result)
    {
        var attempt = RequireAttempt(attemptId);

        if (!string.Equals(result.AttemptId, attemptId, StringComparison.Ordinal))
        {
            throw new InvalidValueException("result must describe the attempt it is recorded on");
        }

        if (attempt.Result is not null)
        {
            throw new DuplicateException($"attempt '{attemptId}' already recorded a result");
        }

        if (PayloadLimits.ByteLength(result.Summary) > PayloadLimits.MaxOutcomeSummaryBytes)
        {
            throw new InvalidValueException(
                $"summary exceeds {PayloadLimits.MaxOutcomeSummaryBytes} bytes; large evidence must travel as an artifact reference (doc 150 NFR-008)");
        }

        attempt.Result = result;
        return result;
    }

    /// <summary>
    /// Accepts the single logical outcome of an execution. Duplicate acceptance
    /// for the same execution is rejected even when the attempt differs
    /// (doc 151 §4.2 invariant 4).
    /// </summary>
    public Outcome AcceptOutcome(string executionId, Outcome outcome)
    {
        var execution = RequireExecution(executionId);

        if (execution.Outcome is { } existing)
        {
            throw new DuplicateException(
                WorkflowValidator.IsDuplicateOutcome(existing, outcome)
                    ? $"execution '{executionId}' already accepted outcome '{existing.OutcomeId}'"
                    : $"execution '{executionId}' already accepted an outcome");
        }

        if (!string.Equals(outcome.ExecutionId, executionId, StringComparison.Ordinal))
        {
            throw new InvalidValueException("outcome must name the execution it resolves");
        }

        var attempt = RequireAttempt(outcome.AcceptedAttemptId);
        var result = attempt.Result
            ?? throw new InvalidValueException("attempt must record a result before it can be accepted");

        var errors = WorkflowValidator.ValidateOutcomeAcceptance(outcome, execution.Current, attempt.Current, result);
        if (errors.Count > 0)
        {
            throw new InvalidValueException(
                $"outcome rejected: {string.Join("; ", errors.Select(e => $"{e.Field} {e.Reason}"))}");
        }

        execution.Outcome = outcome;
        Audit.Append("server", "outcome.accepted", outcome.OutcomeId,
            $"execution '{executionId}' accepted attempt '{outcome.AcceptedAttemptId}'");
        return outcome;
    }

    public Outcome? OutcomeOf(string executionId) => RequireExecution(executionId).Outcome;

    // ---------------------------------------------------------------------
    // Recovery query contract (doc 150 PR-002)
    // ---------------------------------------------------------------------

    /// <summary>Full current position of a run: stage, iteration, attempt, lease, outcome.</summary>
    public RunSnapshot? Snapshot(string runId)
    {
        if (!_runs.TryGetValue(runId, out var run))
        {
            return null;
        }

        var stages = run.Stages.Select(stage =>
        {
            var executions = stage.Executions.Select(execution => new ExecutionSnapshot(
                execution.Current,
                execution.Outcome,
                execution.Attempts.Select(a => new AttemptSnapshot(a.Current, a.Result)).ToList()
            )).ToList();

            return new StageSnapshot(stage.Current, executions);
        }).ToList();

        return new RunSnapshot(run.Current, stages);
    }
}

/// <summary>
/// The schema version tag recorded on registry-originated transitions. The
/// workflow registry contract is versioned independently from the envelopes
/// (doc 151 §11).
/// </summary>
public static class SchemaVersions
{
    public const string Registry = "registry.v1";
}

public sealed record AttemptSnapshot(ExecutionAttempt Attempt, AttemptResult? Result);

public sealed record ExecutionSnapshot(Execution Execution, Outcome? Outcome, IReadOnlyList<AttemptSnapshot> Attempts);

public sealed record StageSnapshot(StageRun Stage, IReadOnlyList<ExecutionSnapshot> Executions);

public sealed record RunSnapshot(WorkflowRun Run, IReadOnlyList<StageSnapshot> Stages)
{
    public StageSnapshot? ActiveStage => Stages.LastOrDefault(s =>
        !RunTransitions.IsTerminal(s.Stage.State));
}
