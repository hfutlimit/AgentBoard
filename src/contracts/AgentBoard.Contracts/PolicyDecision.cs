// SPDX-License-Identifier: MIT
namespace AgentBoard.Contracts;

/// <summary>
/// The three outcomes a Policy Decision Point may produce (doc 150 PR-005,
/// doc 151 §5.3).
/// </summary>
/// <remarks>
/// <para>
/// The set is closed on purpose. doc 151 §5.3 also fixes two behaviours that a
/// permissive implementation tends to get wrong: an unknown action defaults to
/// <see cref="Deny"/>, and <see cref="RequireApproval"/> on an unattended run
/// (no approval channel) must fail fast into a queryable result instead of
/// hanging.
/// </para>
/// <para>
/// The Operator-facing presets named "Review", "Developer" and "Full" are UX
/// conveniences, not a security boundary (doc 150 PR-005). They compile into a
/// versioned PolicyRevision; they must never appear here as decision values.
/// </para>
/// </remarks>
public enum PolicyDecision
{
    /// <summary>Proceed immediately.</summary>
    Allow,

    /// <summary>Refuse and record the reason.</summary>
    Deny,

    /// <summary>Hold until an Operator approves, or fail fast if none can.</summary>
    RequireApproval,
}

/// <summary>
/// Behaviour rules attached to <see cref="PolicyDecision"/>.
/// </summary>
public static class PolicyDecisions
{
    /// <summary>
    /// Resolves a decision for an action the policy does not recognise.
    /// doc 150 PR-005: "默认策略不能把未知 action 当作允许。"
    /// </summary>
    public static PolicyDecision ForUnknownAction() => PolicyDecision.Deny;

    /// <summary>
    /// Resolves <see cref="PolicyDecision.RequireApproval"/> when no approval
    /// channel exists. doc 151 §5.3: the run must fail fast with a retryable or
    /// non-retryable outcome, never wait indefinitely.
    /// </summary>
    /// <returns>
    /// The decision the enforcement point must act on, together with the
    /// failure category to record.
    /// </returns>
    public static (PolicyDecision Decision, FailureCategory Failure) ForUnattendedRun() =>
        (PolicyDecision.Deny, FailureCategory.ApprovalUnavailable);

    /// <summary>True when the decision permits the action to proceed now.</summary>
    public static bool IsAllowed(PolicyDecision decision) => decision == PolicyDecision.Allow;
}
