// SPDX-License-Identifier: MIT
using AgentBoard.Contracts;
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Workflow;

/// <summary>
/// The authoritative state machine for one run-level entity
/// (doc 151 §4.2 invariant 4, §4.3).
/// </summary>
/// <remarks>
/// <para>
/// The legality rules themselves live in
/// <see cref="AgentBoard.Contracts.RunTransitions"/>, frozen during A0. This
/// type is the acceptance half: it is the only path that may change
/// <see cref="Current"/>, every change must carry a
/// <see cref="TransitionContext"/>, and an illegal move leaves the state
/// untouched rather than half-applied.
/// </para>
/// <para>
/// Persistence is deliberately out of scope here. doc 150 NFR-005 requires
/// recovery from durable state, so the constructor takes the current state
/// rather than always starting from the initial one — a machine rebuilt after a
/// restart resumes from whatever the registry says, with an empty history for
/// the new process.
/// </para>
/// </remarks>
public abstract class RunStateMachine<TState> where TState : struct, Enum
{
    private readonly Func<TState, TState, bool> _isLegal;
    private readonly Func<TState, bool> _isTerminal;
    private readonly List<StateTransition<TState>> _history = new();

    protected RunStateMachine(
        TState current,
        Func<TState, TState, bool> isLegal,
        Func<TState, bool> isTerminal)
    {
        Current = current;
        _isLegal = isLegal ?? throw new ArgumentNullException(nameof(isLegal));
        _isTerminal = isTerminal ?? throw new ArgumentNullException(nameof(isTerminal));
    }

    public TState Current { get; private set; }

    /// <summary>Every transition this machine instance has accepted, in order.</summary>
    public IReadOnlyList<StateTransition<TState>> History => _history;

    public bool IsTerminal => _isTerminal(Current);

    /// <summary>
    /// Accepts a transition to <paramref name="to"/>.
    /// </summary>
    /// <exception cref="IllegalTransitionException">
    /// The move is not legal from <see cref="Current"/>. The state is left
    /// unchanged.
    /// </exception>
    public StateTransition<TState> MoveTo(TState to, TransitionContext context)
    {
        ArgumentNullException.ThrowIfNull(context);

        if (!_isLegal(Current, to))
        {
            throw new IllegalTransitionException(
                $"{typeof(TState).Name} cannot move from {Current} to {to}.");
        }

        var transition = new StateTransition<TState>(
            _history.Count + 1,
            Current,
            to,
            context,
            DateTimeOffset.UtcNow);

        // History first, state second: a caller observing the state after a
        // successful MoveTo must always find the matching history entry.
        _history.Add(transition);
        Current = to;

        return transition;
    }
}

/// <summary>Authoritative machine for <see cref="WorkflowRunState"/>.</summary>
public sealed class WorkflowRunStateMachine : RunStateMachine<WorkflowRunState>
{
    public WorkflowRunStateMachine()
        : this(WorkflowRunState.Draft)
    {
    }

    /// <summary>
    /// Resumes from persisted state. This is the recovery path required by
    /// doc 150 NFR-005; without it a restart would reset a running run to Draft.
    /// </summary>
    public WorkflowRunStateMachine(WorkflowRunState current)
        : base(current, (from, to) => RunTransitions.IsLegal(from, to), RunTransitions.IsTerminal)
    {
    }
}

/// <summary>Authoritative machine for <see cref="StageRunState"/>.</summary>
public sealed class StageRunStateMachine : RunStateMachine<StageRunState>
{
    public StageRunStateMachine()
        : this(StageRunState.Pending)
    {
    }

    public StageRunStateMachine(StageRunState current)
        : base(current, (from, to) => RunTransitions.IsLegal(from, to), RunTransitions.IsTerminal)
    {
    }
}

/// <summary>Authoritative machine for <see cref="ExecutionAttemptState"/>.</summary>
public sealed class ExecutionAttemptStateMachine : RunStateMachine<ExecutionAttemptState>
{
    public ExecutionAttemptStateMachine()
        : this(ExecutionAttemptState.Created)
    {
    }

    public ExecutionAttemptStateMachine(ExecutionAttemptState current)
        : base(current, (from, to) => RunTransitions.IsLegal(from, to), RunTransitions.IsTerminal)
    {
    }
}
