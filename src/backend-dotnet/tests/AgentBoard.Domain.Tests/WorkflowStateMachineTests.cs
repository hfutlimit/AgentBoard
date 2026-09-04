// SPDX-License-Identifier: MIT
using AgentBoard.Contracts;
using AgentBoard.Domain.Common;
using AgentBoard.Domain.Workflow;
using Xunit;

namespace AgentBoard.Domain.Tests;

/// <summary>
/// A1: the authoritative state machine (doc 151 §4.3). Legality itself was
/// frozen in A0; these tests cover the acceptance half — who may change state,
/// what must be recorded, and that a rejected move changes nothing.
/// </summary>
public sealed class WorkflowStateMachineTests
{
    private static TransitionContext Context(string actor = "server", string? causationId = null) =>
        new(actor, "test", "workflow.v1", causationId);

    // -------------------------------------------------------------------------
    // Acceptance and audit trail
    // -------------------------------------------------------------------------

    [Fact]
    public void A_workflow_run_advances_and_records_every_transition()
    {
        var machine = new WorkflowRunStateMachine();

        machine.MoveTo(WorkflowRunState.Queued, Context(causationId: "msg-1"));
        machine.MoveTo(WorkflowRunState.Running, Context());
        var last = machine.MoveTo(WorkflowRunState.Succeeded, Context());

        Assert.Equal(WorkflowRunState.Succeeded, machine.Current);
        Assert.Equal(3, machine.History.Count);
        Assert.Equal(WorkflowRunState.Running, last.From);
        Assert.Equal(WorkflowRunState.Succeeded, last.To);
        Assert.Equal(3, last.Sequence);
    }

    [Fact]
    public void Transition_records_causation_when_there_is_one()
    {
        var machine = new WorkflowRunStateMachine();

        var transition = machine.MoveTo(WorkflowRunState.Queued, Context(causationId: "msg-1"));

        // doc 151 §6.4 dedups and traces by causation; losing it here would
        // break the chain from Server summary back to the message that caused it.
        Assert.Equal("msg-1", transition.Context.CausationId);
    }

    [Fact]
    public void History_sequence_is_gapless_and_monotonic()
    {
        var machine = new ExecutionAttemptStateMachine();

        machine.MoveTo(ExecutionAttemptState.Starting, Context());
        machine.MoveTo(ExecutionAttemptState.Running, Context());
        machine.MoveTo(ExecutionAttemptState.Succeeded, Context());

        // A gap or reorder would silently hide a transition from the audit trail.
        Assert.Equal(new[] { 1, 2, 3 }, machine.History.Select(t => t.Sequence));
    }

    // -------------------------------------------------------------------------
    // Rejection leaves state untouched
    // -------------------------------------------------------------------------

    [Fact]
    public void An_illegal_transition_is_rejected_and_changes_nothing()
    {
        var machine = new WorkflowRunStateMachine();

        Assert.Throws<IllegalTransitionException>(
            () => machine.MoveTo(WorkflowRunState.Running, Context()));

        Assert.Equal(WorkflowRunState.Draft, machine.Current);
        Assert.Empty(machine.History);
    }

    [Fact]
    public void A_terminal_state_cannot_be_reopened()
    {
        var machine = new ExecutionAttemptStateMachine(ExecutionAttemptState.Succeeded);

        Assert.True(machine.IsTerminal);
        Assert.Throws<IllegalTransitionException>(
            () => machine.MoveTo(ExecutionAttemptState.Running, Context()));
        Assert.Equal(ExecutionAttemptState.Succeeded, machine.Current);
    }

    [Fact]
    public void Accepting_the_same_transition_twice_is_rejected()
    {
        var machine = new ExecutionAttemptStateMachine(ExecutionAttemptState.Running);

        machine.MoveTo(ExecutionAttemptState.Succeeded, Context());

        // A duplicate acceptance is how one execution ends up with two outcomes
        // (doc 151 §4.2 invariant 4).
        Assert.Throws<IllegalTransitionException>(
            () => machine.MoveTo(ExecutionAttemptState.Succeeded, Context()));
        Assert.Single(machine.History);
    }

    // -------------------------------------------------------------------------
    // doc 151 §4.3 mandatory context
    // -------------------------------------------------------------------------

    [Fact]
    public void A_transition_without_an_actor_is_rejected()
    {
        Assert.Throws<InvalidValueException>(() => new TransitionContext("  ", "because", "workflow.v1"));
    }

    [Fact]
    public void A_transition_without_a_reason_is_rejected()
    {
        Assert.Throws<InvalidValueException>(() => new TransitionContext("server", "", "workflow.v1"));
    }

    [Fact]
    public void A_transition_without_a_valid_schema_version_is_rejected()
    {
        // doc 151 §11: a durable record must not be silently reinterpreted by
        // the running code version, so the version cannot be optional.
        Assert.Throws<InvalidValueException>(
            () => new TransitionContext("server", "because", "not-a-version"));
    }

    [Fact]
    public void A_transition_without_a_context_is_rejected()
    {
        var machine = new WorkflowRunStateMachine();

        Assert.Throws<ArgumentNullException>(() => machine.MoveTo(WorkflowRunState.Queued, null!));
        Assert.Equal(WorkflowRunState.Draft, machine.Current);
    }

    // -------------------------------------------------------------------------
    // doc 151 §4.2 invariant 2 — changes requested is terminal for its stage
    // -------------------------------------------------------------------------

    [Fact]
    public void Changes_requested_ends_the_review_stage()
    {
        var machine = new StageRunStateMachine(StageRunState.Running);

        machine.MoveTo(StageRunState.ChangesRequested, Context());

        Assert.True(machine.IsTerminal);
        Assert.Equal(StageRunState.ChangesRequested, machine.Current);

        // The follow-up work is a NEW development stage run at iteration+1, not
        // a continuation of this one.
        Assert.Throws<IllegalTransitionException>(
            () => machine.MoveTo(StageRunState.Running, Context()));
    }

    [Fact]
    public void An_approval_wait_can_resume_running()
    {
        var machine = new StageRunStateMachine(StageRunState.Running);

        machine.MoveTo(StageRunState.WaitingApproval, Context());
        machine.MoveTo(StageRunState.Running, Context());

        Assert.Equal(StageRunState.Running, machine.Current);
        Assert.Equal(2, machine.History.Count);
    }

    // -------------------------------------------------------------------------
    // doc 150 NFR-005 recovery
    // -------------------------------------------------------------------------

    [Fact]
    public void A_machine_can_be_rebuilt_from_persisted_state()
    {
        // After a restart the registry supplies the last accepted state. Without
        // this constructor a recovering Server would reset a running run to
        // Draft and re-dispatch work that is already in flight.
        var machine = new WorkflowRunStateMachine(WorkflowRunState.Running);

        Assert.Equal(WorkflowRunState.Running, machine.Current);
        Assert.Empty(machine.History);

        machine.MoveTo(WorkflowRunState.Failed, Context());
        Assert.Equal(WorkflowRunState.Failed, machine.Current);
    }

    [Theory]
    [InlineData(ExecutionAttemptState.Created)]
    [InlineData(ExecutionAttemptState.Starting)]
    [InlineData(ExecutionAttemptState.Running)]
    public void Non_terminal_attempt_states_are_not_terminal(ExecutionAttemptState state)
    {
        Assert.False(new ExecutionAttemptStateMachine(state).IsTerminal);
    }

    [Fact]
    public void An_expired_attempt_is_terminal()
    {
        var machine = new ExecutionAttemptStateMachine(ExecutionAttemptState.Running);

        machine.MoveTo(ExecutionAttemptState.Expired, Context());

        Assert.True(machine.IsTerminal);
    }
}
