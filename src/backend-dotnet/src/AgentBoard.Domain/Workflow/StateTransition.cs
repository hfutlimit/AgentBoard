// SPDX-License-Identifier: MIT
namespace AgentBoard.Domain.Workflow;

/// <summary>
/// An accepted state transition, retained for audit (doc 151 §4.3, doc 150
/// NFR-011).
/// </summary>
/// <remarks>
/// <c>Sequence</c> is the monotonic position within this machine's history.
/// doc 150 NFR-011 requires accepted transitions to be traceable, and a
/// gap-less sequence is what makes a missing or reordered entry detectable
/// rather than merely untidy.
/// </remarks>
public sealed record StateTransition<TState>(
    int Sequence,
    TState From,
    TState To,
    TransitionContext Context,
    DateTimeOffset OccurredAt)
    where TState : struct, Enum;
