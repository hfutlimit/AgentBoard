// SPDX-License-Identifier: MIT
namespace AgentBoard.Contracts;

/// <summary>
/// One execution of a workflow version (doc 151 §4.2).
/// </summary>
/// <remarks>
/// Positional, therefore <see cref="WorkflowVersionId"/> can never change once
/// the run exists. That is how doc 151 §4.2 invariant 1 is enforced: a run
/// cannot silently switch to a newer version mid-flight, because there is no
/// way to write the field.
/// </remarks>
public sealed record WorkflowRun(
    string RunId,
    string WorkflowVersionId,
    WorkflowRunState State,
    DateTimeOffset StartedAt);

/// <summary>
/// One logical stage within a run (doc 151 §4.2).
/// </summary>
/// <param name="Iteration">
/// Business iteration. Review feedback does not create a new stage type; it
/// creates another development StageRun with a higher iteration.
/// </param>
/// <param name="Reason">
/// Why this iteration exists, e.g. <see cref="StageRunReasons.ChangesRequested"/>.
/// Null for the first iteration.
/// </param>
public sealed record StageRun(
    string StageRunId,
    string RunId,
    StageType StageType,
    int Iteration,
    string? Reason,
    StageRunState State);

/// <summary>
/// One logical work item inside a stage (doc 151 §4.2).
/// </summary>
public sealed record Execution(string ExecutionId, string StageRunId);

/// <summary>
/// One physical try at an execution (doc 151 §4.2).
/// </summary>
/// <remarks>
/// doc 151 §4.2 invariant 3: an attempt may be recreated after a crash, a
/// timeout, an auth failure, a lease expiry or a retry-policy decision. Many
/// attempts belong to one execution, and only the Server state machine decides
/// which of them becomes the Outcome.
/// </remarks>
public sealed record ExecutionAttempt(
    string AttemptId,
    string ExecutionId,
    long LeaseEpoch,
    ExecutionAttemptState State);

/// <summary>
/// What one physical attempt produced (doc 151 §4.2).
/// </summary>
/// <remarks>
/// doc 151 §4.2 invariant 4: "AttemptResult 只描述一次物理尝试；Outcome 只由
/// Server 状态机接受一次." A succeeded attempt is necessary but not sufficient
/// for a succeeded stage — the acceptance is a separate act.
/// </remarks>
public sealed record AttemptResult(
    string AttemptId,
    AttemptResultStatus Status,
    FailureCategory FailureCategory,
    string? Summary);

/// <summary>
/// The single logical result the Server accepted for an execution
/// (doc 151 §4.2).
/// </summary>
public sealed record Outcome(
    string OutcomeId,
    string ExecutionId,
    string AcceptedAttemptId,
    DateTimeOffset AcceptedAt);
