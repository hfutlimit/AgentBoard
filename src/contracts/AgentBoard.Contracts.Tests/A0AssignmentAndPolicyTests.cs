using System.Runtime.CompilerServices;
using AgentBoard.Contracts;
using Xunit;

namespace AgentBoard.Contracts.Tests;

/// <summary>
/// A0 contract tests for Assignment fencing and the PDP action schema
/// (doc 151 §5.3, §5.4).
/// </summary>
public sealed class A0AssignmentAndPolicyTests
{
    private static readonly DateTimeOffset IssuedAt =
        new(2026, 9, 4, 0, 0, 0, TimeSpan.Zero);

    // -------------------------------------------------------------------------
    // doc 151 §5.4 Assignment
    // -------------------------------------------------------------------------

    [Fact]
    public void A_well_formed_assignment_is_valid()
    {
        Assert.True(AssignmentValidator.IsValid(ValidAssignment()));
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-1)]
    public void Assignment_rejects_a_non_positive_lease_epoch(long epoch)
    {
        var assignment = ValidAssignment() with { LeaseEpoch = epoch };

        Assert.False(AssignmentValidator.IsValid(assignment));
    }

    [Fact]
    public void Assignment_rejects_an_expiry_at_or_before_issue()
    {
        var assignment = ValidAssignment() with { ExpiresAt = IssuedAt };

        Assert.False(AssignmentValidator.IsValid(assignment));
    }

    [Fact]
    public void Assignment_must_declare_required_capabilities()
    {
        var assignment = ValidAssignment() with
        {
            RequiredCapabilities = Array.Empty<string>(),
        };

        // With no capability the assignment constrains nothing, so any agent
        // could claim work it cannot do.
        Assert.False(AssignmentValidator.IsValid(assignment));
    }

    [Fact]
    public void Assignment_is_immutable_so_reassignment_must_create_a_new_one()
    {
        foreach (var property in typeof(Assignment).GetProperties())
        {
            var setter = property.SetMethod;
            Assert.NotNull(setter);

            var isInitOnly = setter.ReturnParameter
                .GetRequiredCustomModifiers()
                .Any(m => m == typeof(IsExternalInit));

            Assert.True(isInitOnly, $"{property.Name} must be init-only");
        }

        // doc 151 §5.4: reassignment produces a new assignment and lease epoch,
        // which is only enforceable if the old one cannot be edited in place.
        var original = ValidAssignment();
        var reassigned = original with { LeaseEpoch = 4, AttemptId = "attempt-2" };

        Assert.Equal(3, original.LeaseEpoch);
        Assert.Equal("attempt-1", original.AttemptId);
        Assert.Equal(4, reassigned.LeaseEpoch);
    }

    [Fact]
    public void An_expired_lease_is_detectable()
    {
        var assignment = ValidAssignment();

        Assert.False(assignment.IsExpired(IssuedAt.AddMinutes(1)));
        Assert.True(assignment.IsExpired(IssuedAt.AddMinutes(5)));
    }

    // -------------------------------------------------------------------------
    // doc 151 §5.4 / §4.2 invariant 5 fencing
    // -------------------------------------------------------------------------

    [Fact]
    public void A_command_matching_its_assignment_is_accepted()
    {
        var assignment = ValidAssignment();

        Assert.Empty(AssignmentValidator.ValidateCommandAgainstAssignment(
            ValidCommand(assignment), assignment));
    }

    [Fact]
    public void A_command_for_a_superseded_lease_epoch_is_rejected()
    {
        var assignment = ValidAssignment() with { LeaseEpoch = 4 };
        var command = ValidCommand(ValidAssignment());   // still epoch 3

        Assert.NotEmpty(AssignmentValidator.ValidateCommandAgainstAssignment(command, assignment));
    }

    [Fact]
    public void A_result_from_a_superseded_epoch_is_stale()
    {
        var assignment = ValidAssignment() with { LeaseEpoch = 4 };
        var result = new ResultEnvelope
        {
            AssignmentId = assignment.AssignmentId,
            AttemptId = assignment.AttemptId,
            LeaseEpoch = 3,
        };

        // doc 151 §4.2 invariant 5: a late result from an old attempt must not
        // overwrite the newer lease's outcome.
        Assert.True(AssignmentValidator.IsStale(result, assignment));
        Assert.NotEmpty(AssignmentValidator.ValidateResultAgainstAssignment(result, assignment));
    }

    [Fact]
    public void A_result_for_the_current_epoch_is_accepted()
    {
        var assignment = ValidAssignment();
        var result = new ResultEnvelope
        {
            AssignmentId = assignment.AssignmentId,
            AttemptId = assignment.AttemptId,
            LeaseEpoch = assignment.LeaseEpoch,
        };

        Assert.False(AssignmentValidator.IsStale(result, assignment));
        Assert.Empty(AssignmentValidator.ValidateResultAgainstAssignment(result, assignment));
    }

    [Fact]
    public void A_command_must_carry_the_policy_revision_of_its_assignment()
    {
        var assignment = ValidAssignment();
        var command = ValidCommand(assignment) with { PolicyRevisionId = "policy-rev-99" };

        // Otherwise a Node could enforce a different policy than the one the
        // assignment was issued under (doc 151 §5.3).
        Assert.NotEmpty(AssignmentValidator.ValidateCommandAgainstAssignment(command, assignment));
    }

    // -------------------------------------------------------------------------
    // doc 151 §5.3 / doc 150 PR-005 PDP action schema
    // -------------------------------------------------------------------------

    [Fact]
    public void A_well_formed_decision_request_is_valid()
    {
        Assert.True(PolicyValidator.IsValid(ValidRequest()));
    }

    [Fact]
    public void A_decision_request_requires_a_workspace()
    {
        var request = ValidRequest() with { Workspace = null };

        // doc 151 §5.3 lists workspace as a mandatory input; deciding without
        // it is not the decision the baseline describes.
        Assert.False(PolicyValidator.IsValid(request));
    }

    [Fact]
    public void An_unknown_action_kind_is_denied_by_default()
    {
        var request = ValidRequest() with
        {
            Action = new PolicyAction("launch_nuclear_missile", "/"),
        };

        var verdict = PolicyValidator.DefaultDenyForUnknownKind(request);

        Assert.NotNull(verdict);
        Assert.Equal(PolicyDecision.Deny, verdict!.Value.Decision);
        Assert.Equal(FailureCategory.PolicyDenied, verdict.Value.Failure);
    }

    [Fact]
    public void A_known_action_kind_is_not_refused_by_default()
    {
        // Null means "not refused by default", explicitly NOT "allowed": rule
        // evaluation must still decide, and an unmatched rule denies.
        Assert.Null(PolicyValidator.DefaultDenyForUnknownKind(ValidRequest()));
    }

    [Fact]
    public void New_action_kinds_can_be_expressed_without_recompiling()
    {
        // The kind is a string so a provider-specific action can be
        // represented and denied rather than crashing the PDP.
        var unknown = new PolicyAction("provider_specific_thing", "resource");

        Assert.False(PolicyActionKinds.IsKnown(unknown.Kind));
        Assert.NotNull(PolicyValidator.DefaultDenyForUnknownKind(
            ValidRequest() with { Action = unknown }));
    }

    [Fact]
    public void Approval_granted_resolves_to_allow()
    {
        var verdict = PolicyValidator.ResolveApproval(ValidRequest() with { ApprovalGranted = true });

        Assert.Equal(PolicyDecision.Allow, verdict.Decision);
        Assert.Equal(FailureCategory.None, verdict.Failure);
    }

    [Fact]
    public void Approval_missing_fails_fast_instead_of_waiting()
    {
        var verdict = PolicyValidator.ResolveApproval(ValidRequest() with { ApprovalGranted = false });

        // doc 151 §5.3: an unattended run must not hang on an approval that
        // can never arrive.
        Assert.Equal(PolicyDecision.Deny, verdict.Decision);
        Assert.Equal(FailureCategory.ApprovalUnavailable, verdict.Failure);
        Assert.True(FailureCategories.IsRetryable(verdict.Failure));
    }

    [Fact]
    public void Every_known_action_kind_is_a_stable_lowercase_token()
    {
        foreach (var kind in PolicyActionKinds.Known)
        {
            Assert.Equal(kind, kind.ToLowerInvariant());
            Assert.DoesNotContain(' ', kind);
        }
    }

    // -------------------------------------------------------------------------
    // Builders
    // -------------------------------------------------------------------------

    private static Assignment ValidAssignment() => new(
        "assignment-1",
        "run-1",
        "stage-1",
        "exec-1",
        "attempt-1",
        "worker-1",
        "agent.dev.codex",
        "lease-1",
        3,
        new[] { "development" },
        IssuedAt,
        IssuedAt.AddMinutes(5),
        "policy-rev-17");

    private static CommandEnvelope ValidCommand(Assignment assignment) => new()
    {
        MessageId = "msg-1",
        SchemaVersion = "command.v1",
        MessageType = MessageTypes.ExecutionAssign,
        CorrelationId = assignment.WorkflowRunId,
        IdempotencyKey = $"{assignment.AssignmentId}:attempt-1",
        WorkflowRunId = assignment.WorkflowRunId,
        StageRunId = assignment.StageRunId,
        ExecutionId = assignment.ExecutionId,
        AttemptId = assignment.AttemptId,
        AssignmentId = assignment.AssignmentId,
        WorkerId = assignment.WorkerId,
        AgentId = assignment.AgentId,
        Traceparent = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
        LeaseId = assignment.LeaseId,
        LeaseEpoch = assignment.LeaseEpoch,
        PolicyRevisionId = assignment.PolicyRevisionId,
        IssuedAt = assignment.IssuedAt,
        ExpiresAt = assignment.ExpiresAt,
    };

    private static PolicyDecisionRequest ValidRequest() => new(
        new PolicyAction(PolicyActionKinds.WriteFile, "src/main.cs"),
        "agent.dev.codex",
        new[] { "development" },
        StageType.Development,
        "run-1",
        new WorkspaceReference("project-1", "workspace-1", "commit-sha"),
        "policy-rev-17",
        ApprovalGranted: false);
}
