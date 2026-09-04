// SPDX-License-Identifier: MIT
namespace AgentBoard.Contracts;

/// <summary>
/// The classified reason an operation did not succeed (doc 150 PR-011, PR-012).
/// </summary>
/// <remarks>
/// doc 150 PR-011 requires a failure to be distinguishable by reading a field,
/// never by parsing a natural-language error string. Every value here maps to
/// one of the named cases in that requirement.
/// </remarks>
public enum FailureCategory
{
    /// <summary>No failure. Present so the field can be non-nullable.</summary>
    None = 0,

    /// <summary>The local PDP denied the action (doc 150 PR-005).</summary>
    PolicyDenied,

    /// <summary>A REQUIRE_APPROVAL decision had no approval channel available.</summary>
    ApprovalUnavailable,

    /// <summary>Provider credentials expired; needs the official login flow again.</summary>
    AuthExpired,

    /// <summary>The provider process failed, crashed, or produced unusable output.</summary>
    ProviderFailure,

    /// <summary>Broker or WSS transport failed, including publish-confirm timeout.</summary>
    TransportFailure,

    /// <summary>The attempt's lease elapsed before a result was accepted.</summary>
    LeaseExpired,

    /// <summary>A result arrived for a superseded lease epoch and was rejected.</summary>
    StaleResult,

    /// <summary>Schema major mismatch, or a required field was absent.</summary>
    SchemaRejection,

    /// <summary>A handoff artifact was missing or failed its checksum.</summary>
    ArtifactUnavailable,
}

/// <summary>
/// Retry classification for <see cref="FailureCategory"/> (doc 150 PR-012:
/// "每类失败必须有 retryable/non-retryable 分类、backoff 上限、DLQ/人工处理路径
/// 和最终可查询状态").
/// </summary>
public static class FailureCategories
{
    /// <summary>
    /// True when the same logical operation may be attempted again without an
    /// external intervention.
    /// </summary>
    /// <remarks>
    /// The classification is deliberately conservative:
    /// <list type="bullet">
    /// <item><see cref="FailureCategory.PolicyDenied"/> is not retryable —
    /// retrying the identical action against the identical policy revision
    /// yields the identical denial, so it is a pointless load and a misleading
    /// audit trail.</item>
    /// <item><see cref="FailureCategory.LeaseExpired"/> and
    /// <see cref="FailureCategory.StaleResult"/> are not retryable as-is:
    /// recovery means a NEW attempt under a NEW lease epoch, not a re-send of
    /// the old one (doc 151 §4.2 invariant 5).</item>
    /// <item><see cref="FailureCategory.SchemaRejection"/> is not retryable
    /// because no number of retries turns an incompatible major version into a
    /// compatible one.</item>
    /// <item><see cref="FailureCategory.AuthExpired"/> and
    /// <see cref="FailureCategory.ApprovalUnavailable"/> are retryable only
    /// after a human or provider-side change; the retry is bounded and ends in
    /// a queryable state either way.</item>
    /// </list>
    /// </remarks>
    public static bool IsRetryable(FailureCategory category) => category switch
    {
        FailureCategory.None => false,
        FailureCategory.PolicyDenied => false,
        FailureCategory.LeaseExpired => false,
        FailureCategory.StaleResult => false,
        FailureCategory.SchemaRejection => false,
        FailureCategory.AuthExpired => true,
        FailureCategory.ApprovalUnavailable => true,
        FailureCategory.ProviderFailure => true,
        FailureCategory.TransportFailure => true,
        FailureCategory.ArtifactUnavailable => true,
        _ => false,
    };

    /// <summary>
    /// True when the category must end in a DLQ / operator quarantine rather
    /// than being silently absorbed by retries.
    /// </summary>
    public static bool RequiresOperatorAction(FailureCategory category) =>
        !IsRetryable(category) && category != FailureCategory.None;
}
