// SPDX-License-Identifier: MIT
using System.Globalization;

namespace AgentBoard.Contracts;

/// <summary>
/// The closed set of broker message types (doc 151 §5.5, §5.6).
/// </summary>
/// <remarks>
/// doc 151 §10 rule 8 requires unknown things to be refused rather than
/// guessed at, so a message type outside this set is rejected during
/// validation instead of being routed by convention.
/// </remarks>
public static class MessageTypes
{
    public const string ExecutionAssign = "execution.assign";
    public const string ExecutionCancel = "execution.cancel";
    public const string ExecutionResult = "execution.result";
    public const string ExecutionSummary = "execution.summary";

    public static IReadOnlySet<string> Known { get; } = new HashSet<string>(StringComparer.Ordinal)
    {
        ExecutionAssign,
        ExecutionCancel,
        ExecutionResult,
        ExecutionSummary,
    };

    public static bool IsKnown(string? messageType) =>
        !string.IsNullOrWhiteSpace(messageType) && Known.Contains(messageType);
}

/// <summary>
/// Size ceilings for anything that travels over the broker or lands in the
/// Server database (doc 150 NFR-008, doc 151 §5.6).
/// </summary>
/// <remarks>
/// NFR-008: detailed events, stdout, tool output and file contents must not
/// enter the Server DB or MQ without a bound. The remedy is a reference, not a
/// bigger column — which is why the envelopes carry
/// <c>PayloadReference</c> / <c>ArtifactReferences</c> alongside the inline
/// field rather than relying on the caller to remember.
/// </remarks>
public static class PayloadLimits
{
    /// <summary>Largest inline command payload before a reference is required.</summary>
    public const int MaxInlinePayloadBytes = 64 * 1024;

    /// <summary>Largest outcome summary the Server will persist.</summary>
    public const int MaxOutcomeSummaryBytes = 8 * 1024;

    public static int ByteLength(string? value) =>
        value is null ? 0 : System.Text.Encoding.UTF8.GetByteCount(value);
}

/// <summary>
/// The outcome of one physical attempt, as reported in
/// <see cref="ResultEnvelope.ResultStatus"/> (doc 151 §5.6).
/// </summary>
/// <remarks>
/// <c>ChangesRequested</c> is here rather than as a stage type because it is
/// Review's business result: it drives a new development StageRun iteration
/// (doc 151 §4.2 invariant 2).
/// </remarks>
public enum AttemptResultStatus
{
    Succeeded,
    Failed,
    Cancelled,
    Expired,

    /// <summary>Review asked for changes; a new development iteration follows.</summary>
    ChangesRequested,
}

/// <summary>
/// A reference to a large object that must not travel inline (doc 151 §7).
/// </summary>
/// <param name="Uri">Location of the artifact.</param>
/// <param name="Sha256">Lowercase hex digest, used to detect tampering.</param>
/// <param name="Size">Byte length of the artifact.</param>
/// <param name="ExpiresAt">End of the retention window, when one applies.</param>
/// <remarks>
/// doc 151 §7: "artifact 必须有 checksum、权限、生命周期和可用性状态。" Without
/// a checksum a handoff cannot tell a corrupted artifact from a changed one,
/// and without a lifetime the store grows without bound.
/// </remarks>
public sealed record ArtifactReference(
    string Uri,
    string Sha256,
    long Size,
    DateTimeOffset? ExpiresAt = null)
{
    /// <summary>True when the digest is a well-formed 64-character hex string.</summary>
    public bool HasWellFormedDigest()
    {
        if (Sha256.Length != 64) return false;

        foreach (var c in Sha256)
        {
            var isDigit = c is >= '0' and <= '9';
            var isLowerHex = c is >= 'a' and <= 'f';
            var isUpperHex = c is >= 'A' and <= 'F';
            if (!isDigit && !isLowerHex && !isUpperHex) return false;
        }

        return true;
    }

    /// <summary>
    /// True when the retention window has elapsed. A null expiry means the
    /// artifact is retained until policy deletes it, which is not the same as
    /// "available forever".
    /// </summary>
    public bool IsExpired(DateTimeOffset now) => ExpiresAt.HasValue && now >= ExpiresAt.Value;
}

/// <summary>
/// The payload of an <c>execution.assign</c> command: the assignment plus the
/// handoff the source stage produced, when one is required (doc 151 §7:
/// "target stage 只依赖 HandoffContext"). The immutable context is included
/// in the durable command so execution never depends on a second, lossy HTTP
/// fetch; the id remains independently queryable in the Server registry.
/// </summary>
public sealed record AssignCommandPayload(
    Assignment Assignment,
    string? HandoffId = null,
    HandoffContext? Handoff = null,
    string TaskContext = "{}",
    string? ProviderId = null,
    StageType? StageType = null,
    string? NodeId = null,
    WorkspaceReference? Workspace = null,
    string? WorkItemType = null,
    int? WorkItemId = null,
    string? TaskType = null);

/// <summary>
/// The workspace a stage runs against (doc 151 §7).
/// </summary>
/// <remarks>
/// doc 151 §7: "shared workspace 必须有 owner/version/concurrency 规则；不能把
/// '同一目录'当作一致性协议。" The version is what makes two agents sharing a
/// directory safe to reason about; the directory alone is not an agreement.
/// </remarks>
public sealed record WorkspaceReference(
    string ProjectId,
    string WorkspaceId,
    string BaseVersion);
