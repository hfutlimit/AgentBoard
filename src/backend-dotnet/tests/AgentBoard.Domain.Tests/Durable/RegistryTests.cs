// SPDX-License-Identifier: MIT
using AgentBoard.Contracts;
using AgentBoard.Domain.Common;
using AgentBoard.Domain.Workflow.Durable;
using Xunit;

namespace AgentBoard.Domain.Tests.Durable;

/// <summary>
/// Registry semantics required by doc 150 PR-001/PR-002 and doc 151 §4.2:
/// immutable versions, legal-only transitions, one outcome per execution, and
/// review feedback expressed as a development iteration.
/// </summary>
public class RegistryTests
{
    [Fact]
    public void PublishVersion_rejects_invalid_graph_and_duplicate_ids()
    {
        var fixture = new PlaneFixture();

        var broken = fixture.Version with
        {
            Nodes = new[] { PlaneFixture.Node(StageType.Development, StageType.Qa) },
        };
        Assert.Throws<InvalidValueException>(() => fixture.Plane.Registry.PublishVersion(broken));

        Assert.Throws<DuplicateException>(() => fixture.Plane.Registry.PublishVersion(fixture.Version));
    }

    [Fact]
    public void Run_is_bound_to_its_version_and_cannot_be_rewritten()
    {
        var fixture = new PlaneFixture();
        var snapshot = fixture.Plane.Registry.Snapshot("run-1")!;

        Assert.Equal(fixture.Version.VersionId, snapshot.Run.WorkflowVersionId);

        // Records are immutable value objects: a `with` copy never rewrites
        // the run the registry holds, and no registry API exists to rebind it.
        var detached = snapshot.Run with { WorkflowVersionId = "version-other" };
        Assert.NotSame(snapshot.Run, detached);
        Assert.Equal(
            fixture.Version.VersionId,
            fixture.Plane.Registry.RequireRun("run-1").Current.WorkflowVersionId);
        Assert.Null(typeof(WorkflowRegistry).GetMethod("RebindRunVersion"));
    }

    [Fact]
    public void PublishVersion_refuses_a_hash_that_does_not_describe_its_graph()
    {
        var fixture = new PlaneFixture();

        // "Immutable" only holds if publishing freezes the collections and
        // proves the hash: a caller-mutated node list with the old string, or
        // a mismatched string outright, must fail closed (doc 151 §12 inv 1).
        var tamperedHash = fixture.Version with { ContentHash = "sha256:deadbeef" };
        Assert.Throws<AgentBoard.Domain.Common.InvalidValueException>(
            () => fixture.Plane.Registry.PublishVersion(tamperedHash));

        var published = fixture.Plane.Registry.RequireVersion(fixture.Version.VersionId);
        Assert.Throws<System.NotSupportedException>(() =>
        {
            // The stored copy must reject mutation even though the original
            // List was passed in by the publisher.
            ((System.Collections.Generic.IList<WorkflowNode>)published.Nodes)
                .Add(PlaneFixture.Node(StageType.Proposal));
        });

        // An array copy would still allow element REPLACEMENT via the IList
        // indexer; the read-only wrapper must refuse that too.
        Assert.Throws<System.NotSupportedException>(() =>
        {
            ((System.Collections.Generic.IList<WorkflowNode>)published.Nodes)[0] =
                PlaneFixture.Node(StageType.Proposal);
        });

        // Nested collections are frozen as well: the node's own
        // AllowedTransitions list is no longer the caller's mutable List.
        Assert.Throws<System.NotSupportedException>(() =>
        {
            ((System.Collections.Generic.IList<StageType>)published.Nodes[0].AllowedTransitions)
                .Add(StageType.Qa);
        });
    }

    [Fact]
    public void Illegal_stage_transition_leaves_state_untouched()
    {
        var fixture = new PlaneFixture();
        fixture.Plane.Registry.AddStage("run-1", "stg-x", StageType.Review, 1, null);

        // Pending -> Succeeded is not a legal move (must pass Assigned/Running).
        Assert.Throws<IllegalTransitionException>(() =>
            fixture.Plane.Registry.MoveStage("stg-x", StageRunState.Succeeded, PlaneFixture.Ctx("skip ahead")));

        Assert.Equal(StageRunState.Pending, fixture.Plane.Registry.RequireStage("stg-x").Current.State);
    }

    [Fact]
    public void Outcome_is_accepted_once_per_execution_even_across_attempts()
    {
        var fixture = new PlaneFixture();
        fixture.CompleteDevelopment();

        var execution = fixture.Plane.Registry.RequireExecution(fixture.ExecutionId);
        Assert.NotNull(execution.Outcome);

        // A second, different outcome for the same execution must be refused.
        Assert.Throws<DuplicateException>(() => fixture.Plane.Registry.AcceptOutcome(
            fixture.ExecutionId,
            new Outcome("out-second", fixture.ExecutionId, execution.Attempts[0].Current.AttemptId, fixture.Now)));
    }

    [Fact]
    public void Outcome_acceptance_requires_terminal_attempt_with_recorded_result()
    {
        var fixture = new PlaneFixture();
        fixture.DispatchDev();

        var attemptId = fixture.Plane.Registry.RequireExecution(fixture.ExecutionId)
            .LatestAttempt!.Current.AttemptId;

        Assert.Throws<InvalidValueException>(() => fixture.Plane.Registry.AcceptOutcome(
            fixture.ExecutionId, new Outcome("out-early", fixture.ExecutionId, attemptId, fixture.Now)));
    }

    [Fact]
    public void Oversized_summary_is_refused_at_the_boundary()
    {
        var fixture = new PlaneFixture();
        fixture.DispatchDev();
        var attemptId = fixture.Plane.Registry.RequireExecution(fixture.ExecutionId)
            .LatestAttempt!.Current.AttemptId;

        var huge = new string('x', PayloadLimits.MaxOutcomeSummaryBytes + 1);

        var verdict = fixture.Plane.Results.Process(
            fixture.Result(AttemptResultStatus.Succeeded, summary: huge));

        Assert.Equal(ResultOutcomeKind.RejectedSchema, verdict.Kind);
        Assert.Null(fixture.Plane.Registry.RequireAttempt(attemptId).Result);
    }

    [Fact]
    public void Changes_requested_creates_development_iteration_not_fix_stage()
    {
        var fixture = new PlaneFixture();
        fixture.CompleteDevelopment();
        fixture.DispatchReview();

        var verdict = fixture.Plane.Results.Process(
            fixture.Result(AttemptResultStatus.ChangesRequested, FailureCategory.None));

        Assert.Equal(ResultOutcomeKind.Accepted, verdict.Kind);
        Assert.NotNull(verdict.CreatedIteration);
        Assert.Equal(StageType.Development, verdict.CreatedIteration!.StageType);
        Assert.Equal(2, verdict.CreatedIteration.Iteration);
        Assert.Equal(StageRunReasons.ChangesRequested, verdict.CreatedIteration.Reason);

        var review = fixture.Plane.Registry.RequireStage("stg-rev-1");
        Assert.Equal(StageRunState.ChangesRequested, review.Current.State);

        // No sixth stage type exists, and the review stage itself is terminal.
        Assert.True(RunTransitions.IsTerminal(StageRunState.ChangesRequested));
    }

    [Fact]
    public void Stage_added_with_unknown_type_fails_closed()
    {
        var fixture = new PlaneFixture();

        Assert.Throws<InvalidValueException>(() =>
            fixture.Plane.Registry.AddStage("run-1", "stg-bogus", StageType.Proposal, 1, null));
    }

    [Fact]
    public void Snapshot_exposes_stage_iteration_attempt_and_outcome()
    {
        var fixture = new PlaneFixture();
        fixture.CompleteDevelopment();

        var snapshot = fixture.Plane.Registry.Snapshot("run-1")!;
        var dev = snapshot.Stages.Single();

        Assert.Equal(StageRunState.Succeeded, dev.Stage.State);
        Assert.Single(dev.Executions);
        var execution = dev.Executions.Single();
        Assert.NotNull(execution.Outcome);
        Assert.Single(execution.Attempts);
        Assert.Equal(ExecutionAttemptState.Succeeded, execution.Attempts[0].Attempt.State);
        Assert.NotNull(execution.Attempts[0].Result);
    }
}
