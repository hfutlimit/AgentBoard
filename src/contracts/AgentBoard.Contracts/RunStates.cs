// SPDX-License-Identifier: MIT
namespace AgentBoard.Contracts;

/// <summary>Lifecycle of a single workflow run (doc 151 §4.3).</summary>
public enum WorkflowRunState
{
    Draft,
    Queued,
    Running,
    Succeeded,
    Failed,
    Cancelled,
}

/// <summary>Lifecycle of one logical stage (doc 151 §4.3).</summary>
/// <remarks>
/// <see cref="ChangesRequested"/> is a business outcome of Review, not a stage
/// type. It is what causes a new <see cref="StageType.Development"/> StageRun
/// to be created rather than a "fix" stage.
/// </remarks>
public enum StageRunState
{
    Pending,
    Assigned,
    Running,
    Succeeded,
    ChangesRequested,
    Failed,
    Cancelled,
    WaitingApproval,
}

/// <summary>Lifecycle of one physical provider attempt (doc 151 §4.3).</summary>
/// <remarks>
/// A stage may have many attempts; only the Server state machine may accept an
/// Outcome, so a succeeded attempt is necessary but not sufficient for a
/// succeeded stage (doc 151 §4.2 invariant 4).
/// </remarks>
public enum ExecutionAttemptState
{
    Created,
    Starting,
    Running,
    Succeeded,
    Failed,
    Cancelled,
    Expired,
}

/// <summary>
/// The legal state transitions of the three run-level state machines
/// (doc 151 §4.3), plus the single entry point used to reject any other move.
/// </summary>
/// <remarks>
/// doc 151 §4.3: "状态迁移只能由拥有相应权威状态的组件接受，并且必须记录
/// actor、reason、causation 和 schema version。" The actor/reason bookkeeping
/// belongs to the registry that owns the state; what this type encodes is the
/// "legal move" half, so an illegal transition is rejected before any
/// bookkeeping happens rather than being written and reconciled later.
/// </remarks>
public static class RunTransitions
{
    private static readonly Dictionary<WorkflowRunState, HashSet<WorkflowRunState>> WorkflowRun = new()
    {
        [WorkflowRunState.Draft] = new HashSet<WorkflowRunState> { WorkflowRunState.Queued, WorkflowRunState.Cancelled },
        [WorkflowRunState.Queued] = new HashSet<WorkflowRunState> { WorkflowRunState.Running, WorkflowRunState.Cancelled },
        [WorkflowRunState.Running] = new HashSet<WorkflowRunState>
        {
            WorkflowRunState.Succeeded,
            WorkflowRunState.Failed,
            WorkflowRunState.Cancelled,
        },
        // Terminal states have no outgoing transitions.
        [WorkflowRunState.Succeeded] = new HashSet<WorkflowRunState>(),
        [WorkflowRunState.Failed] = new HashSet<WorkflowRunState>(),
        [WorkflowRunState.Cancelled] = new HashSet<WorkflowRunState>(),
    };

    private static readonly Dictionary<StageRunState, HashSet<StageRunState>> StageRun = new()
    {
        [StageRunState.Pending] = new HashSet<StageRunState> { StageRunState.Assigned, StageRunState.Cancelled },
        [StageRunState.Assigned] = new HashSet<StageRunState> { StageRunState.Running, StageRunState.Cancelled },
        [StageRunState.Running] = new HashSet<StageRunState>
        {
            StageRunState.Succeeded,
            StageRunState.ChangesRequested,
            StageRunState.Failed,
            StageRunState.Cancelled,
            StageRunState.WaitingApproval,
        },
        // An approval wait resolves by returning to Running, or by ending.
        [StageRunState.WaitingApproval] = new HashSet<StageRunState>
        {
            StageRunState.Running,
            StageRunState.Failed,
            StageRunState.Cancelled,
        },
        // ChangesRequested is terminal for the review stage: the follow-up work
        // is a NEW development StageRun with a higher iteration, never a
        // transition of this one.
        [StageRunState.ChangesRequested] = new HashSet<StageRunState>(),
        [StageRunState.Succeeded] = new HashSet<StageRunState>(),
        [StageRunState.Failed] = new HashSet<StageRunState>(),
        [StageRunState.Cancelled] = new HashSet<StageRunState>(),
    };

    private static readonly Dictionary<ExecutionAttemptState, HashSet<ExecutionAttemptState>> Attempt = new()
    {
        [ExecutionAttemptState.Created] = new HashSet<ExecutionAttemptState>
        {
            ExecutionAttemptState.Starting,
            ExecutionAttemptState.Cancelled,
        },
        [ExecutionAttemptState.Starting] = new HashSet<ExecutionAttemptState>
        {
            ExecutionAttemptState.Running,
            ExecutionAttemptState.Failed,
            ExecutionAttemptState.Cancelled,
            ExecutionAttemptState.Expired,
        },
        [ExecutionAttemptState.Running] = new HashSet<ExecutionAttemptState>
        {
            ExecutionAttemptState.Succeeded,
            ExecutionAttemptState.Failed,
            ExecutionAttemptState.Cancelled,
            ExecutionAttemptState.Expired,
        },
        [ExecutionAttemptState.Succeeded] = new HashSet<ExecutionAttemptState>(),
        [ExecutionAttemptState.Failed] = new HashSet<ExecutionAttemptState>(),
        [ExecutionAttemptState.Cancelled] = new HashSet<ExecutionAttemptState>(),
        [ExecutionAttemptState.Expired] = new HashSet<ExecutionAttemptState>(),
    };

    public static bool IsLegal(WorkflowRunState from, WorkflowRunState to) =>
        WorkflowRun.TryGetValue(from, out var allowed) && allowed.Contains(to);

    public static bool IsLegal(StageRunState from, StageRunState to) =>
        StageRun.TryGetValue(from, out var allowed) && allowed.Contains(to);

    public static bool IsLegal(ExecutionAttemptState from, ExecutionAttemptState to) =>
        Attempt.TryGetValue(from, out var allowed) && allowed.Contains(to);

    public static bool IsTerminal(WorkflowRunState state) => WorkflowRun[state].Count == 0;

    public static bool IsTerminal(StageRunState state) => StageRun[state].Count == 0;

    public static bool IsTerminal(ExecutionAttemptState state) => Attempt[state].Count == 0;
}
