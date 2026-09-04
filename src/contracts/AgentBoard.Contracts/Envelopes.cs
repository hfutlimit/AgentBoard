// SPDX-License-Identifier: MIT
namespace AgentBoard.Contracts;

/// <summary>
/// The fields every broker message carries (doc 151 §5.5, §5.6).
/// </summary>
/// <remarks>
/// Properties are initialised to empty rather than declared <c>required</c>
/// because these types are deserialisation targets: an envelope arrives off the
/// wire possibly malformed, and the contract has to be able to represent that
/// state so <see cref="EnvelopeValidator"/> can reject it with a reason. A
/// type that cannot be constructed without valid data cannot report
/// <em>which</em> field was wrong.
/// <para>
/// Every id is a string even where it looks numeric. These are opaque
/// identifiers minted by whichever side owns the entity, and doc 151 §6.4
/// dedups on them directly — parsing them into a narrower type would let an
/// unrelated upstream id collide with a local one.
/// </para>
/// </remarks>
public abstract record EnvelopeBase
{
    /// <summary>Identity of this message instance (doc 151 §6.4).</summary>
    public string MessageId { get; init; } = string.Empty;

    /// <summary>Contract version of this envelope, e.g. <c>command.v1</c>.</summary>
    public string SchemaVersion { get; init; } = string.Empty;

    /// <summary>One of <see cref="MessageTypes"/>.</summary>
    public string MessageType { get; init; } = string.Empty;

    /// <summary>Ties every message of one workflow run together.</summary>
    public string CorrelationId { get; init; } = string.Empty;

    /// <summary>The message that caused this one, when there was one.</summary>
    public string? CausationId { get; init; }

    /// <summary>Business-operation dedup key (doc 151 §6.4).</summary>
    public string IdempotencyKey { get; init; } = string.Empty;

    public string WorkflowRunId { get; init; } = string.Empty;
    public string StageRunId { get; init; } = string.Empty;
    public string ExecutionId { get; init; } = string.Empty;
    public string AttemptId { get; init; } = string.Empty;
    public string AssignmentId { get; init; } = string.Empty;
    public string WorkerId { get; init; } = string.Empty;
    public string AgentId { get; init; } = string.Empty;

    /// <summary>W3C trace context; required by doc 150 PR-011 / doc 151 §8.1.</summary>
    public string? Traceparent { get; init; }
}

/// <summary>
/// Server to Node work assignment (doc 151 §5.5).
/// </summary>
public sealed record CommandEnvelope : EnvelopeBase
{
    public string LeaseId { get; init; } = string.Empty;

    /// <summary>
    /// Monotonic fencing token. doc 151 §5.4: the Server accepts state updates
    /// only for the current epoch, so a stale Node cannot overwrite a newer
    /// assignment.
    /// </summary>
    public long LeaseEpoch { get; init; }

    public DateTimeOffset IssuedAt { get; init; }
    public DateTimeOffset ExpiresAt { get; init; }

    /// <summary>Inline payload, kept under <see cref="PayloadLimits.MaxInlinePayloadBytes"/>.</summary>
    public string Payload { get; init; } = string.Empty;

    /// <summary>Reference used instead of the inline payload when it is too large.</summary>
    public string? PayloadReference { get; init; }

    /// <summary>Policy revision the Node must enforce for this assignment.</summary>
    public string PolicyRevisionId { get; init; } = string.Empty;
}

/// <summary>
/// Node to Server attempt outcome (doc 151 §5.6).
/// </summary>
/// <remarks>
/// <para>
/// This type has no field for a prompt, a credential, full stdout, tool
/// payloads or file contents — that absence is the requirement, not an
/// omission (doc 151 §5.6, doc 150 NFR-008). Large evidence travels as
/// <see cref="ArtifactReferences"/>.
/// </para>
/// <para>
/// doc 151 §4.2 invariant 4: an attempt result describes one physical try;
/// only the Server state machine accepts an Outcome. A succeeded
/// <see cref="AttemptResultStatus"/> is therefore necessary but not sufficient
/// for a succeeded stage.
/// </para>
/// </remarks>
public sealed record ResultEnvelope : EnvelopeBase
{
    public long LeaseEpoch { get; init; }
    public AttemptResultStatus ResultStatus { get; init; }

    /// <summary>Required when <see cref="ResultStatus"/> is not <c>Succeeded</c>.</summary>
    public FailureCategory FailureCategory { get; init; } = FailureCategory.None;

    public string? OutcomeSummary { get; init; }

    public IReadOnlyList<ArtifactReference> ArtifactReferences { get; init; } =
        Array.Empty<ArtifactReference>();

    public string? CommitOrVersion { get; init; }
    public IReadOnlyList<string> TestEvidence { get; init; } = Array.Empty<string>();
    public IReadOnlyList<string> ReviewFindings { get; init; } = Array.Empty<string>();
    public string? UsageSummary { get; init; }

    public DateTimeOffset CreatedAt { get; init; }
}

/// <summary>
/// A Node-local execution event (doc 151 §5.7), CloudEvents-compatible in
/// shape without requiring a specific SDK.
/// </summary>
/// <remarks>
/// Node events stay local except for permitted summaries (doc 151 §5.7:
/// "Local Portal 保存详细事件；Server 只接收符合权限和 redaction policy 的
/// summary/event reference").
/// </remarks>
public sealed record EventEnvelope
{
    public string EventId { get; init; } = string.Empty;

    /// <summary>
    /// Event origin, e.g. <c>node://{worker-id}/attempt/{attempt-id}</c>.
    /// doc 151 §5.7 requires source + event_id to be globally dedupable.
    /// </summary>
    public string Source { get; init; } = string.Empty;

    /// <summary>Dotted event type, e.g. <c>agentboard.execution.tool_call</c>.</summary>
    public string EventType { get; init; } = string.Empty;

    public string SchemaVersion { get; init; } = string.Empty;

    public DateTimeOffset Time { get; init; }

    /// <summary>The entity the event is about, normally the attempt id.</summary>
    public string Subject { get; init; } = string.Empty;

    public string CorrelationId { get; init; } = string.Empty;
    public string? CausationId { get; init; }
    public string? Traceparent { get; init; }

    /// <summary>Opaque JSON payload.</summary>
    public string Data { get; init; } = string.Empty;

    /// <summary>The globally unique dedup key mandated by doc 151 §5.7.</summary>
    public string DedupKey => string.Concat(Source, "|", EventId);
}
