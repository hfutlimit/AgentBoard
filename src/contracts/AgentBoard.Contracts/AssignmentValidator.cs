// SPDX-License-Identifier: MIT
namespace AgentBoard.Contracts;

/// <summary>
/// Validation and fencing checks for <see cref="Assignment"/>
/// (doc 151 §5.4).
/// </summary>
public static class AssignmentValidator
{
    public static IReadOnlyList<EnvelopeError> Validate(Assignment assignment)
    {
        var errors = new List<EnvelopeError>();

        Require(errors, nameof(assignment.AssignmentId), assignment.AssignmentId);
        Require(errors, nameof(assignment.WorkflowRunId), assignment.WorkflowRunId);
        Require(errors, nameof(assignment.StageRunId), assignment.StageRunId);
        Require(errors, nameof(assignment.ExecutionId), assignment.ExecutionId);
        Require(errors, nameof(assignment.AttemptId), assignment.AttemptId);
        Require(errors, nameof(assignment.WorkerId), assignment.WorkerId);
        Require(errors, nameof(assignment.AgentId), assignment.AgentId);
        Require(errors, nameof(assignment.LeaseId), assignment.LeaseId);
        Require(errors, nameof(assignment.PolicyRevisionId), assignment.PolicyRevisionId);

        if (assignment.LeaseEpoch < 1)
        {
            errors.Add(new EnvelopeError(
                nameof(assignment.LeaseEpoch), "must be greater than or equal to 1"));
        }

        if (assignment.ExpiresAt <= assignment.IssuedAt)
        {
            errors.Add(new EnvelopeError(
                nameof(assignment.ExpiresAt), "must be later than IssuedAt"));
        }

        if (assignment.RequiredCapabilities.Count == 0)
        {
            errors.Add(new EnvelopeError(
                nameof(assignment.RequiredCapabilities),
                "must declare at least one capability; otherwise nothing constrains who may run this"));
        }

        return errors;
    }

    /// <summary>
    /// Checks a command against the assignment it claims to be issued for.
    /// </summary>
    public static IReadOnlyList<EnvelopeError> ValidateCommandAgainstAssignment(
        CommandEnvelope command,
        Assignment assignment)
    {
        var errors = new List<EnvelopeError>();

        if (!string.Equals(command.AssignmentId, assignment.AssignmentId, StringComparison.Ordinal))
        {
            errors.Add(new EnvelopeError(
                nameof(command.AssignmentId), $"must equal assignment '{assignment.AssignmentId}'"));
        }

        if (!string.Equals(command.LeaseId, assignment.LeaseId, StringComparison.Ordinal))
        {
            errors.Add(new EnvelopeError(
                nameof(command.LeaseId), $"must equal lease '{assignment.LeaseId}'"));
        }

        if (command.LeaseEpoch != assignment.LeaseEpoch)
        {
            errors.Add(new EnvelopeError(
                nameof(command.LeaseEpoch),
                $"must equal the assignment's epoch {assignment.LeaseEpoch}"));
        }

        if (!string.Equals(command.AttemptId, assignment.AttemptId, StringComparison.Ordinal))
        {
            errors.Add(new EnvelopeError(
                nameof(command.AttemptId), "must equal the attempt the assignment covers"));
        }

        if (!string.Equals(command.PolicyRevisionId, assignment.PolicyRevisionId, StringComparison.Ordinal))
        {
            errors.Add(new EnvelopeError(
                nameof(command.PolicyRevisionId),
                "must equal the policy revision the assignment was issued under"));
        }

        Match(errors, nameof(command.WorkflowRunId), command.WorkflowRunId, assignment.WorkflowRunId);
        Match(errors, nameof(command.StageRunId), command.StageRunId, assignment.StageRunId);
        Match(errors, nameof(command.ExecutionId), command.ExecutionId, assignment.ExecutionId);
        Match(errors, nameof(command.WorkerId), command.WorkerId, assignment.WorkerId);
        Match(errors, nameof(command.AgentId), command.AgentId, assignment.AgentId);

        return errors;
    }

    /// <summary>
    /// Checks a result against the lease that was supposed to produce it
    /// (doc 151 §5.4, §4.2 invariant 5).
    /// </summary>
    public static IReadOnlyList<EnvelopeError> ValidateResultAgainstAssignment(
        ResultEnvelope result,
        Assignment assignment)
    {
        var errors = new List<EnvelopeError>();

        if (!string.Equals(result.AssignmentId, assignment.AssignmentId, StringComparison.Ordinal))
        {
            errors.Add(new EnvelopeError(
                nameof(result.AssignmentId), $"must equal assignment '{assignment.AssignmentId}'"));
        }

        if (result.LeaseEpoch != assignment.LeaseEpoch)
        {
            errors.Add(new EnvelopeError(
                nameof(result.LeaseEpoch),
                $"must equal the assignment's epoch {assignment.LeaseEpoch}; a differing epoch is stale"));
        }

        if (!string.Equals(result.AttemptId, assignment.AttemptId, StringComparison.Ordinal))
        {
            errors.Add(new EnvelopeError(
                nameof(result.AttemptId), "must equal the attempt the assignment covers"));
        }


        Match(errors, nameof(result.WorkflowRunId), result.WorkflowRunId, assignment.WorkflowRunId);
        Match(errors, nameof(result.StageRunId), result.StageRunId, assignment.StageRunId);
        Match(errors, nameof(result.ExecutionId), result.ExecutionId, assignment.ExecutionId);
        Match(errors, nameof(result.WorkerId), result.WorkerId, assignment.WorkerId);
        Match(errors, nameof(result.AgentId), result.AgentId, assignment.AgentId);

        return errors;
    }

    /// <summary>
    /// True when a result was produced under a superseded lease
    /// (doc 151 §4.2 invariant 5).
    /// </summary>
    public static bool IsStale(ResultEnvelope result, Assignment assignment) =>
        result.LeaseEpoch != assignment.LeaseEpoch;

    public static bool IsValid(Assignment assignment) => Validate(assignment).Count == 0;

    private static void Match(List<EnvelopeError> errors, string field, string? actual, string expected)
    {
        if (!string.Equals(actual, expected, StringComparison.Ordinal))
        {
            errors.Add(new EnvelopeError(field, $"must equal assignment value '{expected}'"));
        }
    }

    private static void Require(List<EnvelopeError> errors, string field, string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            errors.Add(new EnvelopeError(field, "is required"));
        }
    }
}
