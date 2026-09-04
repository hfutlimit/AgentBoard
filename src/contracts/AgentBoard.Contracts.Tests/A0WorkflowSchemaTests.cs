using System.Runtime.CompilerServices;
using AgentBoard.Contracts;
using Xunit;

namespace AgentBoard.Contracts.Tests;

/// <summary>
/// A0 contract tests for the workflow graph and the run hierarchy. Each test
/// names the doc 151 clause it enforces.
/// </summary>
public sealed class A0WorkflowSchemaTests
{
    private static readonly DateTimeOffset StartedAt =
        new(2026, 9, 4, 0, 0, 0, TimeSpan.Zero);

    // -------------------------------------------------------------------------
    // doc 151 §4.1 graph
    // -------------------------------------------------------------------------

    [Fact]
    public void A_well_formed_version_is_valid()
    {
        Assert.True(WorkflowValidator.IsValid(ValidVersion()));
    }

    [Fact]
    public void Graph_must_be_closed_under_its_own_transitions()
    {
        var version = ValidVersion() with
        {
            Nodes = ValidVersion().Nodes
                .Append(ValidVersion().Nodes[0] with
                {
                    NodeId = "qa",
                    StageType = StageType.Qa,
                    AllowedTransitions = new[] { StageType.Proposal },
                })
                .ToList(),
        };

        // A transition to an undeclared stage strands the run at runtime; it
        // is far cheaper to refuse it at publish time.
        Assert.False(WorkflowValidator.IsValid(version));
    }

    [Fact]
    public void Nodes_must_have_positive_budgets()
    {
        var version = ValidVersion() with
        {
            Nodes = new[]
            {
                ValidVersion().Nodes[0] with { Budget = new StageBudget(0, 300) },
            },
        };

        Assert.False(WorkflowValidator.IsValid(version));
    }

    [Fact]
    public void Duplicate_node_ids_are_rejected()
    {
        var version = ValidVersion() with
        {
            Nodes = new[] { ValidVersion().Nodes[0], ValidVersion().Nodes[0] },
        };

        Assert.False(WorkflowValidator.IsValid(version));
    }

    [Fact]
    public void Content_hash_preserves_field_boundaries()
    {
        var original = ValidVersion().Nodes[0];
        var left = new[] { original with { RequiredCapability = "a|b", InputContract = "c" } };
        var right = new[] { original with { RequiredCapability = "a", InputContract = "b|c" } };

        // Delimiter concatenation made these two different graphs produce the
        // same canonical string. The digest must be over an unambiguous token
        // stream or it cannot prove which graph was published.
        Assert.NotEqual(
            WorkflowGraph.ComputeContentHash(left),
            WorkflowGraph.ComputeContentHash(right));
    }

    [Fact]
    public void A_node_cannot_carry_a_script_shell_command_or_prompt()
    {
        // doc 151 §4.1 forbids any workflow DSL, shell hook or generic script
        // execution. A node declares constraints; it does not execute.
        foreach (var name in new[] { "Script", "ShellCommand", "Command", "Hook", "Prompt", "CommandLine" })
        {
            Assert.Null(typeof(WorkflowNode).GetProperty(name));
        }
    }

    // -------------------------------------------------------------------------
    // doc 151 §4.2 invariant 1 / §12 invariant 1 — immutable version, pinned run
    // -------------------------------------------------------------------------

    [Fact]
    public void Workflow_version_is_immutable()
    {
        // CanWrite is true for init-only accessors too, so the IsExternalInit
        // modreq is the only reliable signal.
        foreach (var property in typeof(WorkflowVersion).GetProperties())
        {
            var setter = property.SetMethod;
            Assert.NotNull(setter);

            var isInitOnly = setter.ReturnParameter
                .GetRequiredCustomModifiers()
                .Any(m => m == typeof(IsExternalInit));

            Assert.True(isInitOnly, $"{property.Name} must be init-only");
        }
    }

    [Fact]
    public void A_run_cannot_change_its_workflow_version()
    {
        var run = ValidRun();

        // WorkflowRun is positional, so there is no way to write the field.
        // This is what makes invariant 1 structural rather than a convention.
        foreach (var property in typeof(WorkflowRun).GetProperties())
        {
            var setter = property.SetMethod;
            Assert.NotNull(setter);

            var isInitOnly = setter.ReturnParameter
                .GetRequiredCustomModifiers()
                .Any(m => m == typeof(IsExternalInit));

            Assert.True(isInitOnly, $"{property.Name} must be init-only");
        }

        var copy = run with { State = WorkflowRunState.Running };
        Assert.Equal("version-1", copy.WorkflowVersionId);
    }

    [Fact]
    public void A_stage_run_must_belong_to_its_run()
    {
        var stage = ValidStage() with { RunId = "run-other" };

        Assert.NotEmpty(WorkflowValidator.ValidateStageRunAgainstRun(stage, ValidRun()));
        Assert.Empty(WorkflowValidator.ValidateStageRunAgainstRun(ValidStage(), ValidRun()));
    }

    [Fact]
    public void An_execution_must_belong_to_its_stage_run()
    {
        var execution = new Execution("exec-1", "stage-other");

        Assert.NotEmpty(WorkflowValidator.ValidateExecutionAgainstStageRun(execution, ValidStage()));
    }

    [Fact]
    public void An_attempt_must_belong_to_its_execution_and_carry_a_lease_epoch()
    {
        var attempt = new ExecutionAttempt("attempt-1", "exec-other", 1, ExecutionAttemptState.Created);

        Assert.NotEmpty(WorkflowValidator.ValidateAttemptAgainstExecution(attempt, ValidExecution()));

        var badEpoch = new ExecutionAttempt("attempt-1", "exec-1", 0, ExecutionAttemptState.Created);
        Assert.NotEmpty(WorkflowValidator.ValidateAttemptAgainstExecution(badEpoch, ValidExecution()));
    }

    // -------------------------------------------------------------------------
    // doc 151 §4.2 invariant 2 — changes requested is an iteration, not a fix
    // -------------------------------------------------------------------------

    [Fact]
    public void Changes_requested_produces_a_development_iteration()
    {
        var review = ValidStage() with
        {
            StageType = StageType.Review,
            State = StageRunState.ChangesRequested,
            Iteration = 2,
        };

        var next = WorkflowValidator.NextDevelopmentIteration(review, "stage-2");

        Assert.Equal(StageType.Development, next.StageType);
        Assert.Equal(3, next.Iteration);
        Assert.Equal(StageRunReasons.ChangesRequested, next.Reason);
        Assert.Equal(StageRunState.Pending, next.State);
        Assert.Equal(review.RunId, next.RunId);
    }

    [Fact]
    public void Succession_requires_development_specifically()
    {
        var review = ValidStage() with
        {
            StageType = StageType.Review,
            State = StageRunState.ChangesRequested,
        };

        var bogus = new StageRun(
            "stage-2", review.RunId, StageType.Development, review.Iteration + 1,
            StageRunReasons.ChangesRequested, StageRunState.Pending);

        // The type system already prevents a "fix" stage; this proves the
        // succession still requires Development specifically.
        Assert.Empty(WorkflowValidator.ValidateChangesRequestedSuccession(review, bogus));

        var wrongStage = bogus with { StageType = StageType.Qa };
        Assert.NotEmpty(WorkflowValidator.ValidateChangesRequestedSuccession(review, wrongStage));
    }

    [Fact]
    public void A_succession_that_does_not_increment_the_iteration_is_rejected()
    {
        var review = ValidStage() with
        {
            StageType = StageType.Review,
            State = StageRunState.ChangesRequested,
            Iteration = 2,
        };

        var stale = WorkflowValidator.NextDevelopmentIteration(review, "stage-2") with { Iteration = 2 };

        Assert.NotEmpty(WorkflowValidator.ValidateChangesRequestedSuccession(review, stale));
    }

    [Fact]
    public void Succession_from_a_stage_that_did_not_request_changes_is_rejected()
    {
        var review = ValidStage() with
        {
            StageType = StageType.Review,
            State = StageRunState.Succeeded,
        };

        Assert.Throws<InvalidOperationException>(
            () => WorkflowValidator.NextDevelopmentIteration(review, "stage-2"));
    }

    [Fact]
    public void Iteration_survives_repeated_review_rounds()
    {
        // Three review rounds must yield three distinct development iterations,
        // which is exactly what a "fix" stage type would have hidden.
        var stage = ValidStage() with { StageType = StageType.Development, Iteration = 1 };
        var iterations = new List<int> { stage.Iteration };

        for (var round = 0; round < 3; round++)
        {
            var review = stage with
            {
                StageRunId = $"review-{round}",
                StageType = StageType.Review,
                Iteration = stage.Iteration,
                State = StageRunState.ChangesRequested,
            };

            stage = WorkflowValidator.NextDevelopmentIteration(review, $"dev-{round}");
            iterations.Add(stage.Iteration);
        }

        Assert.Equal(new[] { 1, 2, 3, 4 }, iterations);
    }

    // -------------------------------------------------------------------------
    // doc 151 §4.2 invariants 4 and 5 — outcome acceptance
    // -------------------------------------------------------------------------

    [Fact]
    public void A_terminal_attempt_result_can_be_accepted_once()
    {
        var execution = ValidExecution();
        var attempt = new ExecutionAttempt("attempt-1", execution.ExecutionId, 3, ExecutionAttemptState.Succeeded);
        var result = new AttemptResult(attempt.AttemptId, AttemptResultStatus.Succeeded, FailureCategory.None, null);
        var outcome = new Outcome("outcome-1", execution.ExecutionId, attempt.AttemptId, StartedAt);

        Assert.Empty(WorkflowValidator.ValidateOutcomeAcceptance(outcome, execution, attempt, result));
    }

    [Fact]
    public void A_still_running_attempt_cannot_be_accepted()
    {
        var execution = ValidExecution();
        var attempt = new ExecutionAttempt("attempt-1", execution.ExecutionId, 3, ExecutionAttemptState.Running);
        var result = new AttemptResult(attempt.AttemptId, AttemptResultStatus.Succeeded, FailureCategory.None, null);
        var outcome = new Outcome("outcome-1", execution.ExecutionId, attempt.AttemptId, StartedAt);

        Assert.NotEmpty(WorkflowValidator.ValidateOutcomeAcceptance(outcome, execution, attempt, result));
    }

    [Fact]
    public void A_failed_accepted_result_must_name_its_failure_category()
    {
        var execution = ValidExecution();
        var attempt = new ExecutionAttempt("attempt-1", execution.ExecutionId, 3, ExecutionAttemptState.Failed);
        var result = new AttemptResult(attempt.AttemptId, AttemptResultStatus.Failed, FailureCategory.None, null);
        var outcome = new Outcome("outcome-1", execution.ExecutionId, attempt.AttemptId, StartedAt);

        Assert.NotEmpty(WorkflowValidator.ValidateOutcomeAcceptance(outcome, execution, attempt, result));
    }

    [Fact]
    public void A_second_outcome_for_the_same_execution_is_a_duplicate()
    {
        var execution = ValidExecution();
        var first = new Outcome("outcome-1", execution.ExecutionId, "attempt-1", StartedAt);
        var second = new Outcome("outcome-2", execution.ExecutionId, "attempt-2", StartedAt);

        // doc 151 §4.2 invariant 4: an Outcome is accepted once. Multiple
        // attempts may run; only one may become the outcome.
        Assert.True(WorkflowValidator.IsDuplicateOutcome(first, second));
        Assert.False(WorkflowValidator.IsDuplicateOutcome(first, first));
    }

    [Fact]
    public void An_outcome_cannot_name_an_attempt_from_another_execution()
    {
        var execution = ValidExecution();
        var attempt = new ExecutionAttempt("attempt-1", "exec-other", 3, ExecutionAttemptState.Succeeded);
        var result = new AttemptResult(attempt.AttemptId, AttemptResultStatus.Succeeded, FailureCategory.None, null);
        var outcome = new Outcome("outcome-1", execution.ExecutionId, attempt.AttemptId, StartedAt);

        Assert.NotEmpty(WorkflowValidator.ValidateOutcomeAcceptance(outcome, execution, attempt, result));
    }

    // -------------------------------------------------------------------------
    // Builders
    // -------------------------------------------------------------------------

    private static WorkflowVersion ValidVersion() => new(
        "version-1",
        "definition-1",
        1,
        "workflow.v1",
        new[]
        {
            new WorkflowNode(
                "design", StageType.Design, "design", "{}", "{}",
                new[] { StageType.Development }, "retry-standard", "policy-requirements",
                new StageBudget(1800, 300), true),
            new WorkflowNode(
                "development", StageType.Development, "development", "{}", "{}",
                new[] { StageType.Review }, "retry-standard", "policy-requirements",
                new StageBudget(3600, 600), true),
            new WorkflowNode(
                "review", StageType.Review, "review", "{}", "{}",
                new[] { StageType.Development, StageType.Qa }, "retry-standard", "policy-requirements",
                new StageBudget(1200, 300), true),
            new WorkflowNode(
                "qa", StageType.Qa, "qa", "{}", "{}",
                Array.Empty<StageType>(), "retry-standard", "policy-requirements",
                new StageBudget(1800, 300), true),
        },
        "sha256:graph");

    private static WorkflowRun ValidRun() => new("run-1", "version-1", WorkflowRunState.Running, StartedAt);

    private static StageRun ValidStage() => new(
        "stage-1", "run-1", StageType.Development, 1, null, StageRunState.Running);

    private static Execution ValidExecution() => new("exec-1", "stage-1");
}
