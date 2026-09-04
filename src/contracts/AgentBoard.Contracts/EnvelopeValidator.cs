// SPDX-License-Identifier: MIT
namespace AgentBoard.Contracts;

/// <summary>A single failed contract check, naming the field and the reason.</summary>
/// <remarks>
/// doc 150 NFR-006: a core path must never fail with a single context-free
/// error string. Naming the field is the minimum bar for an operator to act.
/// </remarks>
public sealed record EnvelopeError(string Field, string Reason);

/// <summary>
/// Validation for the A0 cross-boundary contracts.
/// </summary>
/// <remarks>
/// Validation is separated from the records themselves so a malformed message
/// can be inspected before being rejected: the durable-accept path
/// (doc 151 §5.5) has to record <em>why</em> a message was refused, and a
/// constructor that throws can only report the first problem it hit.
/// </remarks>
public static class EnvelopeValidator
{
    public static IReadOnlyList<EnvelopeError> Validate(CommandEnvelope command)
    {
        var errors = new List<EnvelopeError>();
        ValidateHeader(command, errors, "command");

        // A command envelope carrying a result-family type (or vice versa)
        // would route by convention; the type set per envelope is closed.
        if (command.MessageType is not (MessageTypes.ExecutionAssign or MessageTypes.ExecutionCancel))
        {
            errors.Add(new EnvelopeError(
                nameof(command.MessageType),
                $"'{command.MessageType}' is not a command message type; expected '{MessageTypes.ExecutionAssign}' or '{MessageTypes.ExecutionCancel}'"));
        }

        Require(errors, nameof(command.LeaseId), command.LeaseId);
        Require(errors, nameof(command.PolicyRevisionId), command.PolicyRevisionId);

        // A lease epoch of zero or less cannot fence anything: doc 151 §5.4
        // relies on the epoch being monotonic to reject stale results.
        if (command.LeaseEpoch < 1)
        {
            errors.Add(new EnvelopeError(
                nameof(command.LeaseEpoch), "must be greater than or equal to 1"));
        }

        if (command.ExpiresAt <= command.IssuedAt)
        {
            errors.Add(new EnvelopeError(
                nameof(command.ExpiresAt), "must be later than IssuedAt"));
        }

        // NFR-008: an oversized inline payload must become a reference rather
        // than being pushed through the broker anyway.
        var payloadBytes = PayloadLimits.ByteLength(command.Payload);
        if (payloadBytes > PayloadLimits.MaxInlinePayloadBytes)
        {
            errors.Add(new EnvelopeError(
                nameof(command.Payload),
                $"is {payloadBytes} bytes, over the {PayloadLimits.MaxInlinePayloadBytes} byte inline limit; " +
                "use PayloadReference"));
        }

        if (payloadBytes > 0 && !string.IsNullOrWhiteSpace(command.PayloadReference))
        {
            errors.Add(new EnvelopeError(
                nameof(command.PayloadReference),
                "must not be set when Payload is set; the two are alternatives"));
        }

        return errors;
    }

    public static IReadOnlyList<EnvelopeError> Validate(ResultEnvelope result)
    {
        var errors = new List<EnvelopeError>();
        ValidateHeader(result, errors, "result");

        if (result.MessageType is not (MessageTypes.ExecutionResult or MessageTypes.ExecutionSummary))
        {
            errors.Add(new EnvelopeError(
                nameof(result.MessageType),
                $"'{result.MessageType}' is not a result message type; expected '{MessageTypes.ExecutionResult}' or '{MessageTypes.ExecutionSummary}'"));
        }

        if (result.LeaseEpoch < 1)
        {
            errors.Add(new EnvelopeError(
                nameof(result.LeaseEpoch), "must be greater than or equal to 1"));
        }

        if (!Enum.IsDefined(result.ResultStatus))
        {
            errors.Add(new EnvelopeError(
                nameof(result.ResultStatus), $"'{result.ResultStatus}' is not a defined status"));
        }

        // A failure without a category is unactionable: doc 150 PR-011 requires
        // the reason to be readable from a field, never parsed from prose.
        // ChangesRequested is exempt in both directions — it is a business
        // outcome of review (doc 151 §4.2 invariant 2), not a failure, and
        // therefore carries no category at all.
        if (result.ResultStatus is not (AttemptResultStatus.Succeeded or AttemptResultStatus.ChangesRequested)
            && result.FailureCategory == FailureCategory.None)
        {
            errors.Add(new EnvelopeError(
                nameof(result.FailureCategory),
                "is required when ResultStatus is not Succeeded"));
        }

        if (result.ResultStatus is AttemptResultStatus.Succeeded or AttemptResultStatus.ChangesRequested
            && result.FailureCategory != FailureCategory.None)
        {
            errors.Add(new EnvelopeError(
                nameof(result.FailureCategory), "must be None when ResultStatus is not a failure"));
        }

        var summaryBytes = PayloadLimits.ByteLength(result.OutcomeSummary);
        if (summaryBytes > PayloadLimits.MaxOutcomeSummaryBytes)
        {
            errors.Add(new EnvelopeError(
                nameof(result.OutcomeSummary),
                $"is {summaryBytes} bytes, over the {PayloadLimits.MaxOutcomeSummaryBytes} byte limit"));
        }

        ValidateArtifacts(errors, nameof(result.ArtifactReferences), result.ArtifactReferences);

        return errors;
    }

    public static IReadOnlyList<EnvelopeError> Validate(EventEnvelope envelope)
    {
        var errors = new List<EnvelopeError>();

        Require(errors, nameof(envelope.EventId), envelope.EventId);
        Require(errors, nameof(envelope.Source), envelope.Source);
        Require(errors, nameof(envelope.EventType), envelope.EventType);
        Require(errors, nameof(envelope.Subject), envelope.Subject);
        Require(errors, nameof(envelope.CorrelationId), envelope.CorrelationId);

        // doc 151 §5.7: source + event_id must be globally dedupable, which
        // only holds if the source is a structured origin rather than a free
        // string that two nodes might format differently.
        if (!envelope.Source.StartsWith("node://", StringComparison.Ordinal))
        {
            errors.Add(new EnvelopeError(
                nameof(envelope.Source), "must start with 'node://' to be globally dedupable"));
        }

        ValidateContractVersion(errors, nameof(envelope.SchemaVersion), envelope.SchemaVersion, "execution-event");

        return errors;
    }

    public static IReadOnlyList<EnvelopeError> Validate(HandoffContext handoff)
    {
        var errors = new List<EnvelopeError>();

        Require(errors, nameof(handoff.HandoffId), handoff.HandoffId);
        Require(errors, nameof(handoff.SourceStageRunId), handoff.SourceStageRunId);
        Require(errors, nameof(handoff.SourceOutcomeId), handoff.SourceOutcomeId);
        Require(errors, nameof(handoff.TaskContext), handoff.TaskContext);

        if (!Enum.IsDefined(handoff.TargetStageType))
        {
            errors.Add(new EnvelopeError(
                nameof(handoff.TargetStageType),
                $"'{handoff.TargetStageType}' is not a defined stage type"));
        }

        ValidateContractVersion(errors, nameof(handoff.ContextVersion), handoff.ContextVersion, "handoff");

        if (handoff.Workspace is null)
        {
            errors.Add(new EnvelopeError(nameof(handoff.Workspace), "is required"));
        }
        else
        {
            Require(errors, "Workspace.ProjectId", handoff.Workspace.ProjectId);
            Require(errors, "Workspace.WorkspaceId", handoff.Workspace.WorkspaceId);
            Require(errors, "Workspace.BaseVersion", handoff.Workspace.BaseVersion);
        }

        // A handoff with no declared capability cannot be assigned: the
        // scheduler would have nothing to match against.
        if (handoff.RequiredCapabilities.Count == 0)
        {
            errors.Add(new EnvelopeError(
                nameof(handoff.RequiredCapabilities), "must declare at least one capability"));
        }

        ValidateArtifacts(errors, nameof(handoff.ArtifactReferences), handoff.ArtifactReferences);

        return errors;
    }

    /// <summary>
    /// Checks the causal and fencing link between a command and the result that
    /// answers it (doc 151 §6.4, §5.4).
    /// </summary>
    public static IReadOnlyList<EnvelopeError> ValidateResultFollowsCommand(
        CommandEnvelope command,
        ResultEnvelope result)
    {
        var errors = new List<EnvelopeError>();

        if (!string.Equals(result.CausationId, command.MessageId, StringComparison.Ordinal))
        {
            errors.Add(new EnvelopeError(
                nameof(result.CausationId), "must equal the command's MessageId"));
        }

        if (!string.Equals(result.CorrelationId, command.CorrelationId, StringComparison.Ordinal))
        {
            errors.Add(new EnvelopeError(
                nameof(result.CorrelationId), "must equal the command's CorrelationId"));
        }

        if (!string.Equals(result.AssignmentId, command.AssignmentId, StringComparison.Ordinal))
        {
            errors.Add(new EnvelopeError(
                nameof(result.AssignmentId), "must equal the command's AssignmentId"));
        }

        if (result.LeaseEpoch != command.LeaseEpoch)
        {
            errors.Add(new EnvelopeError(
                nameof(result.LeaseEpoch),
                "must equal the command's LeaseEpoch; a differing epoch is a stale result"));
        }

        if (!string.Equals(result.IdempotencyKey, command.IdempotencyKey, StringComparison.Ordinal))
        {
            errors.Add(new EnvelopeError(
                nameof(result.IdempotencyKey), "must equal the command's IdempotencyKey"));
        }

        // The identities a result claims must be the identities the command
        // was addressed to. Comparing only the correlation chain would let a
        // different worker's report ride a valid causation id.
        foreach (var (field, expected, actual) in new (string, string, string)[]
        {
            (nameof(result.WorkerId), command.WorkerId, result.WorkerId),
            (nameof(result.AgentId), command.AgentId, result.AgentId),
            (nameof(result.WorkflowRunId), command.WorkflowRunId, result.WorkflowRunId),
            (nameof(result.StageRunId), command.StageRunId, result.StageRunId),
            (nameof(result.ExecutionId), command.ExecutionId, result.ExecutionId),
            (nameof(result.AttemptId), command.AttemptId, result.AttemptId),
        })
        {
            if (!string.Equals(expected, actual, StringComparison.Ordinal))
            {
                errors.Add(new EnvelopeError(
                    field, $"'{actual}' does not match the issued command's '{expected}'"));
            }
        }

        return errors;
    }

    /// <summary>
    /// doc 150 PR-016 / doc 151 §11: an unsupported major version must be
    /// explicitly rejected, while a higher minor stays consumable because
    /// unknown optional fields must be ignored. The expected contract name is
    /// checked too — "result.v1" on a command envelope is a routing error,
    /// not a version nit.
    /// </summary>
    internal const int SupportedMajor = 1;

    private static void ValidateContractVersion(
        List<EnvelopeError> errors, string field, string? schemaVersion, string expectedName)
    {
        if (!SchemaVersion.TryParse(schemaVersion, out var parsed))
        {
            errors.Add(new EnvelopeError(
                field, $"'{schemaVersion}' is not a valid schema version"));
            return;
        }

        if (!string.Equals(parsed.Name, expectedName, StringComparison.Ordinal))
        {
            errors.Add(new EnvelopeError(
                field, $"'{schemaVersion}' names contract '{parsed.Name}'; this message type implements '{expectedName}'"));
            return;
        }

        if (parsed.Major != SupportedMajor)
        {
            errors.Add(new EnvelopeError(
                field,
                $"'{expectedName}.v{parsed.Major}' is not supported; this consumer implements " +
                $"'{expectedName}.v{SupportedMajor}' and must reject unknown majors rather than guess (doc 150 PR-016)"));
        }
    }

    private static void ValidateHeader(EnvelopeBase envelope, List<EnvelopeError> errors, string expectedName)
    {
        Require(errors, nameof(envelope.MessageId), envelope.MessageId);
        Require(errors, nameof(envelope.CorrelationId), envelope.CorrelationId);
        Require(errors, nameof(envelope.IdempotencyKey), envelope.IdempotencyKey);
        Require(errors, nameof(envelope.WorkflowRunId), envelope.WorkflowRunId);
        Require(errors, nameof(envelope.StageRunId), envelope.StageRunId);
        Require(errors, nameof(envelope.ExecutionId), envelope.ExecutionId);
        Require(errors, nameof(envelope.AttemptId), envelope.AttemptId);
        Require(errors, nameof(envelope.AssignmentId), envelope.AssignmentId);
        Require(errors, nameof(envelope.WorkerId), envelope.WorkerId);
        Require(errors, nameof(envelope.AgentId), envelope.AgentId);

        ValidateContractVersion(errors, nameof(envelope.SchemaVersion), envelope.SchemaVersion, expectedName);

        if (!MessageTypes.IsKnown(envelope.MessageType))
        {
            errors.Add(new EnvelopeError(
                nameof(envelope.MessageType),
                $"'{envelope.MessageType}' is not a known message type"));
        }

        // doc 150 PR-011 / doc 151 §8.1: correlation, causation and trace
        // context must propagate end to end, so it is required, not optional.
        if (string.IsNullOrWhiteSpace(envelope.Traceparent))
        {
            errors.Add(new EnvelopeError(
                nameof(envelope.Traceparent),
                "is required so the attempt can be traced from Server summary to Node detail"));
        }
    }

    private static void ValidateArtifacts(
        List<EnvelopeError> errors,
        string field,
        IReadOnlyList<ArtifactReference> artifacts)
    {
        foreach (var artifact in artifacts)
        {
            if (string.IsNullOrWhiteSpace(artifact.Uri))
            {
                errors.Add(new EnvelopeError(field, "contains a reference with an empty Uri"));
            }

            if (!artifact.HasWellFormedDigest())
            {
                errors.Add(new EnvelopeError(
                    field, $"contains '{artifact.Uri}' with a malformed sha256 digest"));
            }

            if (artifact.Size < 0)
            {
                errors.Add(new EnvelopeError(
                    field, $"contains '{artifact.Uri}' with a negative size"));
            }
        }
    }

    private static void Require(List<EnvelopeError> errors, string field, string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            errors.Add(new EnvelopeError(field, "is required"));
        }
    }

    public static bool IsValid(CommandEnvelope command) => Validate(command).Count == 0;

    public static bool IsValid(ResultEnvelope result) => Validate(result).Count == 0;

    public static bool IsValid(EventEnvelope envelope) => Validate(envelope).Count == 0;

    public static bool IsValid(HandoffContext handoff) => Validate(handoff).Count == 0;
}
