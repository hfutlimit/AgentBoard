using AgentBoard.Contracts;
using Xunit;

namespace AgentBoard.Contracts.Tests;

/// <summary>
/// A0 contract tests for the cross-boundary vocabulary. Each test names the
/// doc 150 / doc 151 clause it exists to enforce, so a future change that
/// breaks an invariant fails here rather than at integration time.
/// </summary>
public sealed class A0ContractTests
{
    // -------------------------------------------------------------------------
    // Invariant 2: fix feedback produces a development iteration, not a fix type
    // -------------------------------------------------------------------------

    [Fact]
    public void Stage_type_is_exactly_the_five_baseline_types()
    {
        Assert.Equal(
            new[] { "Proposal", "Design", "Development", "Review", "Qa" },
            Enum.GetNames<StageType>());
    }

    [Fact]
    public void Stage_type_has_no_fix_member()
    {
        // doc 150 §2.2 / doc 151 §4.2 invariant 2. Re-adding Fix is the single
        // easiest way to make "how many iterations did this take" unanswerable.
        Assert.False(Enum.TryParse<StageType>("Fix", ignoreCase: true, out _));
        Assert.DoesNotContain(
            "Fix", Enum.GetNames<StageType>(), StringComparer.OrdinalIgnoreCase);
    }

    [Fact]
    public void Changes_requested_is_terminal_for_its_own_stage_run()
    {
        // The follow-up work is a NEW development StageRun with a higher
        // iteration. If changes_requested could transition onward, the stage
        // could both request changes and later succeed, losing the iteration.
        Assert.True(RunTransitions.IsTerminal(StageRunState.ChangesRequested));
        Assert.False(RunTransitions.IsLegal(
            StageRunState.ChangesRequested, StageRunState.Running));
    }

    // -------------------------------------------------------------------------
    // doc 151 §4.3 state machines
    // -------------------------------------------------------------------------

    [Theory]
    [InlineData(WorkflowRunState.Draft, WorkflowRunState.Queued, true)]
    [InlineData(WorkflowRunState.Queued, WorkflowRunState.Running, true)]
    [InlineData(WorkflowRunState.Running, WorkflowRunState.Succeeded, true)]
    [InlineData(WorkflowRunState.Running, WorkflowRunState.Failed, true)]
    [InlineData(WorkflowRunState.Running, WorkflowRunState.Cancelled, true)]
    [InlineData(WorkflowRunState.Draft, WorkflowRunState.Running, false)]
    [InlineData(WorkflowRunState.Succeeded, WorkflowRunState.Running, false)]
    [InlineData(WorkflowRunState.Failed, WorkflowRunState.Succeeded, false)]
    public void Workflow_run_transitions_match_the_baseline(
        WorkflowRunState from, WorkflowRunState to, bool expected)
    {
        Assert.Equal(expected, RunTransitions.IsLegal(from, to));
    }

    [Theory]
    [InlineData(StageRunState.Pending, StageRunState.Assigned, true)]
    [InlineData(StageRunState.Assigned, StageRunState.Running, true)]
    [InlineData(StageRunState.Running, StageRunState.WaitingApproval, true)]
    [InlineData(StageRunState.WaitingApproval, StageRunState.Running, true)]
    [InlineData(StageRunState.Running, StageRunState.ChangesRequested, true)]
    [InlineData(StageRunState.Pending, StageRunState.Succeeded, false)]
    [InlineData(StageRunState.Succeeded, StageRunState.Running, false)]
    public void Stage_run_transitions_match_the_baseline(
        StageRunState from, StageRunState to, bool expected)
    {
        Assert.Equal(expected, RunTransitions.IsLegal(from, to));
    }

    [Theory]
    [InlineData(ExecutionAttemptState.Created, ExecutionAttemptState.Starting, true)]
    [InlineData(ExecutionAttemptState.Starting, ExecutionAttemptState.Running, true)]
    [InlineData(ExecutionAttemptState.Running, ExecutionAttemptState.Succeeded, true)]
    [InlineData(ExecutionAttemptState.Running, ExecutionAttemptState.Expired, true)]
    [InlineData(ExecutionAttemptState.Created, ExecutionAttemptState.Succeeded, false)]
    [InlineData(ExecutionAttemptState.Succeeded, ExecutionAttemptState.Running, false)]
    public void Attempt_transitions_match_the_baseline(
        ExecutionAttemptState from, ExecutionAttemptState to, bool expected)
    {
        Assert.Equal(expected, RunTransitions.IsLegal(from, to));
    }

    [Fact]
    public void Terminal_states_have_no_outgoing_transitions()
    {
        foreach (var state in Enum.GetValues<WorkflowRunState>())
        {
            if (state is WorkflowRunState.Succeeded or WorkflowRunState.Failed or WorkflowRunState.Cancelled)
            {
                Assert.True(RunTransitions.IsTerminal(state));
            }
        }

        foreach (var state in Enum.GetValues<ExecutionAttemptState>())
        {
            if (state is ExecutionAttemptState.Succeeded
                or ExecutionAttemptState.Failed
                or ExecutionAttemptState.Cancelled
                or ExecutionAttemptState.Expired)
            {
                Assert.True(RunTransitions.IsTerminal(state));
            }
        }
    }

    // -------------------------------------------------------------------------
    // Invariant 9: major mismatch rejected, minor addition tolerated
    // -------------------------------------------------------------------------

    [Fact]
    public void Schema_version_parses_both_major_only_and_major_minor_forms()
    {
        Assert.Equal(new SchemaVersion("command", 1, 0), SchemaVersion.Parse("command.v1"));
        Assert.Equal(new SchemaVersion("command", 1, 3), SchemaVersion.Parse("command.v1.3"));
        Assert.Equal(new SchemaVersion("execution-event", 2, 0), SchemaVersion.Parse("execution-event.v2"));
    }

    [Fact]
    public void Schema_major_mismatch_is_incompatible()
    {
        var producer = SchemaVersion.Parse("command.v2.0");
        var consumer = SchemaVersion.Parse("command.v1.9");

        // A higher minor on the consumer still cannot rescue a major bump.
        Assert.False(producer.IsCompatibleWith(consumer));
        Assert.False(consumer.IsCompatibleWith(producer));
    }

    [Fact]
    public void Higher_minor_from_producer_is_still_consumable()
    {
        // The consumer is required to ignore fields outside its known set, so a
        // producer that adds an optional field must not break it.
        var producer = SchemaVersion.Parse("command.v1.4");
        var consumer = SchemaVersion.Parse("command.v1.1");

        Assert.True(producer.IsCompatibleWith(consumer));
        Assert.True(consumer.IsCompatibleWith(producer));
    }

    [Fact]
    public void Different_contract_names_are_never_compatible()
    {
        Assert.False(SchemaVersion.Parse("command.v1")
            .IsCompatibleWith(SchemaVersion.Parse("result.v1")));
    }

    [Theory]
    [InlineData("command")]
    [InlineData("v1")]
    [InlineData("command.1")]
    [InlineData("")]
    [InlineData("   ")]
    public void Malformed_schema_versions_are_rejected(string value)
    {
        Assert.False(SchemaVersion.TryParse(value, out _));
        Assert.Throws<FormatException>(() => SchemaVersion.Parse(value));
    }

    // -------------------------------------------------------------------------
    // doc 150 PR-005 / invariant 5: policy decisions
    // -------------------------------------------------------------------------

    [Fact]
    public void Unknown_action_defaults_to_deny()
    {
        Assert.Equal(PolicyDecision.Deny, PolicyDecisions.ForUnknownAction());
    }

    [Fact]
    public void Policy_decision_has_exactly_three_outcomes()
    {
        Assert.Equal(
            new[] { "Allow", "Deny", "RequireApproval" },
            Enum.GetNames<PolicyDecision>());
    }

    [Fact]
    public void Require_approval_without_a_channel_fails_fast_with_a_recorded_reason()
    {
        // Waiting forever on an approval that can never arrive is the failure
        // mode doc 151 §5.3 calls out explicitly.
        var (decision, failure) = PolicyDecisions.ForUnattendedRun();

        Assert.Equal(PolicyDecision.Deny, decision);
        Assert.Equal(FailureCategory.ApprovalUnavailable, failure);
        Assert.False(PolicyDecisions.IsAllowed(decision));
    }

    // -------------------------------------------------------------------------
    // doc 150 PR-012: retry classification
    // -------------------------------------------------------------------------

    [Theory]
    [InlineData(FailureCategory.TransportFailure, true)]
    [InlineData(FailureCategory.ProviderFailure, true)]
    [InlineData(FailureCategory.AuthExpired, true)]
    [InlineData(FailureCategory.ArtifactUnavailable, true)]
    [InlineData(FailureCategory.ApprovalUnavailable, true)]
    [InlineData(FailureCategory.PolicyDenied, false)]
    [InlineData(FailureCategory.LeaseExpired, false)]
    [InlineData(FailureCategory.StaleResult, false)]
    [InlineData(FailureCategory.SchemaRejection, false)]
    public void Failure_categories_are_classified(FailureCategory category, bool retryable)
    {
        Assert.Equal(retryable, FailureCategories.IsRetryable(category));
    }

    [Fact]
    public void Non_retryable_failures_require_operator_action()
    {
        // Silent absorption is how a duplicate or stale result ends up
        // unexplainable weeks later.
        foreach (var category in Enum.GetValues<FailureCategory>())
        {
            if (category == FailureCategory.None) continue;

            Assert.Equal(
                !FailureCategories.IsRetryable(category),
                FailureCategories.RequiresOperatorAction(category));
        }
    }

    [Fact]
    public void Every_failure_category_is_classified()
    {
        // A new category with no classification is a gap in doc 150 PR-012,
        // which requires every failure to have a retryable/non-retryable answer.
        foreach (var category in Enum.GetValues<FailureCategory>())
        {
            if (category == FailureCategory.None) continue;

            Assert.Contains(category, new[]
            {
                FailureCategory.PolicyDenied, FailureCategory.ApprovalUnavailable,
                FailureCategory.AuthExpired, FailureCategory.ProviderFailure,
                FailureCategory.TransportFailure, FailureCategory.LeaseExpired,
                FailureCategory.StaleResult, FailureCategory.SchemaRejection,
                FailureCategory.ArtifactUnavailable,
            });
        }
    }
}
