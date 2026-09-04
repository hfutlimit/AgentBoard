// SPDX-License-Identifier: MIT
namespace AgentBoard.Contracts;

/// <summary>
/// Validation for the workflow graph and the run hierarchy
/// (doc 151 §4.1, §4.2).
/// </summary>
public static class WorkflowValidator
{
    public static IReadOnlyList<EnvelopeError> Validate(WorkflowVersion version)
    {
        var errors = new List<EnvelopeError>();

        Require(errors, nameof(version.VersionId), version.VersionId);
        Require(errors, nameof(version.DefinitionId), version.DefinitionId);
        Require(errors, nameof(version.ContentHash), version.ContentHash);

        if (version.Version < 1)
        {
            errors.Add(new EnvelopeError(
                nameof(version.Version), "must be greater than or equal to 1"));
        }

        if (!SchemaVersion.TryParse(version.SchemaVersion, out _))
        {
            errors.Add(new EnvelopeError(
                nameof(version.SchemaVersion),
                $"'{version.SchemaVersion}' is not a valid schema version"));
        }

        if (version.Nodes.Count == 0)
        {
            errors.Add(new EnvelopeError(
                nameof(version.Nodes), "must declare at least one node"));
            return errors;
        }

        var declaredStages = new HashSet<StageType>();
        var seenNodeIds = new HashSet<string>(StringComparer.Ordinal);

        foreach (var node in version.Nodes)
        {
            Require(errors, $"{nameof(version.Nodes)}[].NodeId", node.NodeId);
            Require(errors, $"{nameof(version.Nodes)}[].RequiredCapability", node.RequiredCapability);
            Require(errors, $"{nameof(version.Nodes)}[].RetryPolicyRef", node.RetryPolicyRef);
            Require(errors, $"{nameof(version.Nodes)}[].PolicyRequirements", node.PolicyRequirements);

            if (!seenNodeIds.Add(node.NodeId))
            {
                errors.Add(new EnvelopeError(
                    $"{nameof(version.Nodes)}[].NodeId", $"'{node.NodeId}' is declared more than once"));
            }

            if (!Enum.IsDefined(node.StageType))
            {
                errors.Add(new EnvelopeError(
                    $"{nameof(version.Nodes)}[].StageType",
                    $"'{node.StageType}' is not a defined stage type"));
            }

            if (node.Budget.TimeoutSeconds <= 0)
            {
                errors.Add(new EnvelopeError(
                    $"{nameof(version.Nodes)}[].Budget", "TimeoutSeconds must be positive"));
            }

            if (node.Budget.LeaseSeconds <= 0)
            {
                errors.Add(new EnvelopeError(
                    $"{nameof(version.Nodes)}[].Budget", "LeaseSeconds must be positive"));
            }

            declaredStages.Add(node.StageType);
        }

        // The graph must be closed: a transition to a stage the graph does not
        // declare would strand the run at publish time rather than at runtime.
        foreach (var node in version.Nodes)
        {
            foreach (var target in node.AllowedTransitions)
            {
                if (!declaredStages.Contains(target))
                {
                    errors.Add(new EnvelopeError(
                        $"{nameof(version.Nodes)}[].AllowedTransitions",
                        $"node '{node.NodeId}' transitions to '{target}', which the graph does not declare"));
                }
            }
        }

        return errors;
    }

    // -------------------------------------------------------------------------
    // doc 151 §4.2 invariant 1 — a run stays on one workflow version
    // -------------------------------------------------------------------------

    public static IReadOnlyList<EnvelopeError> ValidateStageRunAgainstRun(
        StageRun stage,
        WorkflowRun run)
    {
        var errors = new List<EnvelopeError>();

        if (!string.Equals(stage.RunId, run.RunId, StringComparison.Ordinal))
        {
            errors.Add(new EnvelopeError(
                nameof(stage.RunId), $"does not belong to run '{run.RunId}'"));
        }

        return errors;
    }

    public static IReadOnlyList<EnvelopeError> ValidateExecutionAgainstStageRun(
        Execution execution,
        StageRun stage)
    {
        var errors = new List<EnvelopeError>();

        if (!string.Equals(execution.StageRunId, stage.StageRunId, StringComparison.Ordinal))
        {
            errors.Add(new EnvelopeError(
                nameof(execution.StageRunId), $"does not belong to stage run '{stage.StageRunId}'"));
        }

        return errors;
    }

    public static IReadOnlyList<EnvelopeError> ValidateAttemptAgainstExecution(
        ExecutionAttempt attempt,
        Execution execution)
    {
        var errors = new List<EnvelopeError>();

        if (!string.Equals(attempt.ExecutionId, execution.ExecutionId, StringComparison.Ordinal))
        {
            errors.Add(new EnvelopeError(
                nameof(attempt.ExecutionId), $"does not belong to execution '{execution.ExecutionId}'"));
        }

        if (attempt.LeaseEpoch < 1)
        {
            errors.Add(new EnvelopeError(
                nameof(attempt.LeaseEpoch), "must be greater than or equal to 1"));
        }

        return errors;
    }

    // -------------------------------------------------------------------------
    // doc 151 §4.2 invariant 2 — changes requested produces a development
    // iteration, never a fix stage
    // -------------------------------------------------------------------------

    /// <summary>
    /// Builds the follow-up development StageRun for a review that asked for
    /// changes.
    /// </summary>
    /// <exception cref="InvalidOperationException">
    /// The source stage run is not a review that ended in
    /// <see cref="StageRunState.ChangesRequested"/>.
    /// </exception>
    public static StageRun NextDevelopmentIteration(StageRun reviewStage, string stageRunId)
    {
        var errors = ValidateChangesRequestedSuccession(
            reviewStage,
            new StageRun(stageRunId, reviewStage.RunId, StageType.Development,
                reviewStage.Iteration + 1, StageRunReasons.ChangesRequested, StageRunState.Pending));

        if (errors.Count > 0)
        {
            throw new InvalidOperationException(
                $"Cannot derive a development iteration: {string.Join("; ", errors.Select(e => $"{e.Field} {e.Reason}"))}");
        }

        return new StageRun(
            stageRunId,
            reviewStage.RunId,
            StageType.Development,
            reviewStage.Iteration + 1,
            StageRunReasons.ChangesRequested,
            StageRunState.Pending);
    }

    public static IReadOnlyList<EnvelopeError> ValidateChangesRequestedSuccession(
        StageRun reviewStage,
        StageRun next)
    {
        var errors = new List<EnvelopeError>();

        if (reviewStage.StageType != StageType.Review)
        {
            errors.Add(new EnvelopeError(
                nameof(reviewStage.StageType), "source must be a review stage"));
        }

        if (reviewStage.State != StageRunState.ChangesRequested)
        {
            errors.Add(new EnvelopeError(
                nameof(reviewStage.State),
                $"source must have ended in {nameof(StageRunState.ChangesRequested)}"));
        }

        if (next.StageType != StageType.Development)
        {
            errors.Add(new EnvelopeError(
                nameof(next.StageType),
                "must be Development; review feedback is an iteration, not a fix stage"));
        }

        if (next.Iteration != reviewStage.Iteration + 1)
        {
            errors.Add(new EnvelopeError(
                nameof(next.Iteration),
                $"must be {reviewStage.Iteration + 1} to follow iteration {reviewStage.Iteration}"));
        }

        if (!string.Equals(next.Reason, StageRunReasons.ChangesRequested, StringComparison.Ordinal))
        {
            errors.Add(new EnvelopeError(
                nameof(next.Reason), $"must be '{StageRunReasons.ChangesRequested}'"));
        }

        if (next.State != StageRunState.Pending)
        {
            errors.Add(new EnvelopeError(
                nameof(next.State), "a new iteration starts Pending"));
        }

        if (!string.Equals(next.RunId, reviewStage.RunId, StringComparison.Ordinal))
        {
            errors.Add(new EnvelopeError(
                nameof(next.RunId), "must stay within the same workflow run"));
        }

        return errors;
    }

    // -------------------------------------------------------------------------
    // doc 151 §4.2 invariants 4 and 5 — one outcome per execution
    // -------------------------------------------------------------------------

    public static IReadOnlyList<EnvelopeError> ValidateOutcomeAcceptance(
        Outcome outcome,
        Execution execution,
        ExecutionAttempt attempt,
        AttemptResult result)
    {
        var errors = new List<EnvelopeError>();

        if (!string.Equals(outcome.ExecutionId, execution.ExecutionId, StringComparison.Ordinal))
        {
            errors.Add(new EnvelopeError(
                nameof(outcome.ExecutionId), $"does not belong to execution '{execution.ExecutionId}'"));
        }

        if (!string.Equals(outcome.AcceptedAttemptId, attempt.AttemptId, StringComparison.Ordinal))
        {
            errors.Add(new EnvelopeError(
                nameof(outcome.AcceptedAttemptId), "must name the attempt whose result was accepted"));
        }

        if (!string.Equals(attempt.ExecutionId, execution.ExecutionId, StringComparison.Ordinal))
        {
            errors.Add(new EnvelopeError(
                nameof(attempt.ExecutionId), "must belong to the execution being resolved"));
        }

        if (!string.Equals(result.AttemptId, attempt.AttemptId, StringComparison.Ordinal))
        {
            errors.Add(new EnvelopeError(
                nameof(result.AttemptId), "must describe the attempt being accepted"));
        }

        // An attempt that is still running has no result to accept.
        if (!RunTransitions.IsTerminal(attempt.State))
        {
            errors.Add(new EnvelopeError(
                nameof(attempt.State), "must be terminal before its result can be accepted"));
        }

        // ChangesRequested is exempt (see EnvelopeValidator): a review asking
        // for an iteration is a business result, not a failure, so the
        // accepted outcome legitimately carries no failure category.
        if (result.Status is not (AttemptResultStatus.Succeeded or AttemptResultStatus.ChangesRequested)
            && result.FailureCategory == FailureCategory.None)
        {
            errors.Add(new EnvelopeError(
                nameof(result.FailureCategory),
                "is required when the accepted result is not a success"));
        }

        return errors;
    }

    /// <summary>
    /// True when <paramref name="candidate"/> would be a second outcome for an
    /// execution that already has one (doc 151 §4.2 invariant 4).
    /// </summary>
    public static bool IsDuplicateOutcome(Outcome existing, Outcome candidate) =>
        string.Equals(existing.ExecutionId, candidate.ExecutionId, StringComparison.Ordinal)
        && !string.Equals(existing.OutcomeId, candidate.OutcomeId, StringComparison.Ordinal);

    public static bool IsValid(WorkflowVersion version) => Validate(version).Count == 0;

    private static void Require(List<EnvelopeError> errors, string field, string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            errors.Add(new EnvelopeError(field, "is required"));
        }
    }
}
