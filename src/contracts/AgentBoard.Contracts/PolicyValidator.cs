// SPDX-License-Identifier: MIT
namespace AgentBoard.Contracts;

/// <summary>
/// Validation and the default-deny rule for policy decision requests
/// (doc 151 §5.3, §10 rule 8; doc 150 PR-005).
/// </summary>
public static class PolicyValidator
{
    public static IReadOnlyList<EnvelopeError> Validate(PolicyDecisionRequest request)
    {
        var errors = new List<EnvelopeError>();

        if (request.Action is null)
        {
            errors.Add(new EnvelopeError(nameof(request.Action), "is required"));
            return errors;
        }

        Require(errors, $"{nameof(request.Action)}.Kind", request.Action.Kind);
        Require(errors, $"{nameof(request.Action)}.Resource", request.Action.Resource);
        Require(errors, nameof(request.AgentId), request.AgentId);
        Require(errors, nameof(request.PolicyRevisionId), request.PolicyRevisionId);
        Require(errors, nameof(request.WorkflowRunId), request.WorkflowRunId);

        if (request.Stage is null || !Enum.IsDefined(request.Stage.Value))
        {
            errors.Add(new EnvelopeError(nameof(request.Stage), "must name a defined workflow stage"));
        }

        if (request.AgentCapabilities is null || request.AgentCapabilities.Count == 0)
        {
            errors.Add(new EnvelopeError(nameof(request.AgentCapabilities), "must declare at least one capability"));
        }

        // doc 151 §5.3 requires the decision to consider the workspace, so one
        // must be supplied; a decision reached without it is not the decision
        // the baseline describes.
        if (request.Workspace is null)
        {
            errors.Add(new EnvelopeError(nameof(request.Workspace), "is required"));
        }
        else
        {
            Require(errors, "Workspace.ProjectId", request.Workspace.ProjectId);
            Require(errors, "Workspace.WorkspaceId", request.Workspace.WorkspaceId);
            Require(errors, "Workspace.BaseVersion", request.Workspace.BaseVersion);
        }

        return errors;
    }

    /// <summary>
    /// Applies the default-deny rule for action kinds the policy does not
    /// recognise.
    /// </summary>
    /// <returns>
    /// A denied decision with <see cref="FailureCategory.PolicyDenied"/> when
    /// the kind is unknown; <c>null</c> when the kind is known, meaning rule
    /// evaluation must produce the decision.
    /// </returns>
    /// <remarks>
    /// doc 150 PR-005: "默认策略不能把未知 action 当作允许。" Returning null for
    /// the known case is deliberate — this type must not imply that a known
    /// action is allowed, only that it is not refused by default.
    /// </remarks>
    public static (PolicyDecision Decision, FailureCategory Failure)? DefaultDenyForUnknownKind(
        PolicyDecisionRequest request)
    {
        if (request.Action is not null && PolicyActionKinds.IsKnown(request.Action.Kind))
        {
            return null;
        }

        return (PolicyDecision.Deny, FailureCategory.PolicyDenied);
    }

    /// <summary>
    /// Resolves a <see cref="PolicyDecision.RequireApproval"/> outcome for a run
    /// that has no approval channel (doc 151 §5.3).
    /// </summary>
    public static (PolicyDecision Decision, FailureCategory Failure) ResolveApproval(
        PolicyDecisionRequest request)
    {
        if (request.ApprovalChannelOpen)
        {
            // A channel exists (Local Portal / designated operator): hold the
            // action until the decision arrives. The waiting stage itself is
            // StageRunState.WaitingApproval, owned by the registry.
            return (PolicyDecision.RequireApproval, FailureCategory.None);
        }

        // This contract-only helper has no approval authority and therefore
        // must never turn a caller-provided boolean into permission. Verified
        // grants are resolved by the Node PDP through IApprovalAuthority.
        // Without a channel, fail fast into a queryable state.
        return (PolicyDecision.Deny, FailureCategory.ApprovalUnavailable);
    }

    public static bool IsValid(PolicyDecisionRequest request) => Validate(request).Count == 0;

    private static void Require(List<EnvelopeError> errors, string field, string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            errors.Add(new EnvelopeError(field, "is required"));
        }
    }
}
