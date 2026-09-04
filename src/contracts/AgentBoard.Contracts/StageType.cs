// SPDX-License-Identifier: MIT
namespace AgentBoard.Contracts;

/// <summary>
/// The five logical stage types of a workflow (doc 151 §4.1, doc 150 §2.2).
/// </summary>
/// <remarks>
/// <para>
/// There is deliberately no <c>Fix</c> member. Review feedback that asks for
/// changes is expressed as a <see cref="StageRunState.ChangesRequested"/>
/// outcome on the review stage, which then drives a new
/// <see cref="Development"/> StageRun with an incremented iteration — not a
/// sixth stage type. Adding <c>Fix</c> would make "how many times did we
/// iterate" unanswerable, because each rework would be a distinct type rather
/// than a distinct iteration of the same stage.
/// </para>
/// <para>
/// This enum is the closed set doc 151 §4.1 describes. A workflow graph that
/// references any other node type must fail closed at publish time.
/// </para>
/// </remarks>
public enum StageType
{
    Proposal,
    Design,
    Development,
    Review,
    Qa,
}
