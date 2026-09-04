// SPDX-License-Identifier: MIT
using AgentBoard.Contracts;
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Workflow;

/// <summary>
/// Who accepted a state transition, why, and under which contract version.
/// </summary>
/// <remarks>
/// doc 151 §4.3: "状态迁移只能由拥有相应权威状态的组件接受，并且必须记录
/// actor、reason、causation 和 schema version."
/// <para>
/// All three of actor, reason and schema version are mandatory. A transition
/// without an actor cannot be attributed, one without a reason cannot be
/// reviewed, and one without a schema version cannot be interpreted correctly
/// after the contract evolves — which doc 151 §11 forbids outright ("durable
/// records 不得被'当前代码版本'静默重解释").
/// </para>
/// </remarks>
public sealed record TransitionContext
{
    public string Actor { get; }
    public string Reason { get; }
    public string SchemaVersion { get; }

    /// <summary>The message or event that caused this transition, when there was one.</summary>
    public string? CausationId { get; }

    public TransitionContext(string actor, string reason, string schemaVersion, string? causationId = null)
    {
        if (string.IsNullOrWhiteSpace(actor))
        {
            throw new InvalidValueException("a transition must record the actor that accepted it");
        }

        if (string.IsNullOrWhiteSpace(reason))
        {
            throw new InvalidValueException("a transition must record why it happened");
        }

        if (!AgentBoard.Contracts.SchemaVersion.TryParse(schemaVersion, out _))
        {
            throw new InvalidValueException(
                $"'{schemaVersion}' is not a valid schema version; expected {{name}}.v{{major}}[.{{minor}}]");
        }

        Actor = actor;
        Reason = reason;
        SchemaVersion = schemaVersion;
        CausationId = causationId;
    }
}
