// SPDX-License-Identifier: MIT
namespace AgentBoard.Contracts;

/// <summary>
/// The time budget a stage node grants to one attempt (doc 151 §4.1).
/// </summary>
/// <param name="TimeoutSeconds">How long the attempt may run before it is cancelled.</param>
/// <param name="LeaseSeconds">How long the assignment lease lasts before renewal.</param>
/// <remarks>
/// Durations are carried as integer seconds rather than an ISO 8601 duration
/// string or a framework time type. Seconds have exactly one encoding, whereas
/// duration strings have several dialects that different stacks parse
/// differently — and a lease budget that one side reads as 30 minutes and the
/// other as 30 seconds is a fencing bug, not a formatting nit.
/// </remarks>
public sealed record StageBudget(long TimeoutSeconds, long LeaseSeconds)
{
    public TimeSpan Timeout => TimeSpan.FromSeconds(TimeoutSeconds);
    public TimeSpan Lease => TimeSpan.FromSeconds(LeaseSeconds);
}

/// <summary>
/// One node of a typed workflow graph (doc 151 §4.1).
/// </summary>
/// <remarks>
/// doc 151 §4.1 fixes what every node must declare, and simultaneously forbids
/// an arbitrary workflow language: "内部可以使用 JSON/YAML 作为经过 schema 校验
/// 的序列化格式，但不提供任意 workflow DSL、shell hook 或通用脚本执行能力."
/// There is therefore no member here that can carry a script, a shell command
/// or a prompt fragment — a node declares constraints, it does not execute.
/// </remarks>
public sealed record WorkflowNode(
    string NodeId,
    StageType StageType,
    string RequiredCapability,
    string InputContract,
    string OutputContract,
    IReadOnlyList<StageType> AllowedTransitions,
    string RetryPolicyRef,
    string PolicyRequirements,
    StageBudget Budget,
    bool HandoffRequired);

/// <summary>
/// The editable definition a version is published from (doc 151 §4.1).
/// </summary>
public sealed record WorkflowDefinition(string DefinitionId, string Name);

/// <summary>
/// An immutable, published workflow version (doc 151 §4.1).
/// </summary>
/// <remarks>
/// <para>
/// doc 151 §4.2 invariant 1: "同一个 WorkflowRun 始终使用同一个 WorkflowVersion"
/// and §12 invariant 1: "发布后的 WorkflowVersion 在运行中不可变". A positional
/// record gives init-only members, so a version cannot be edited once built.
/// </para>
/// <para>
/// <see cref="ContentHash"/> covers the node set. Membership in the record is
/// fixed but the collection objects are not deeply frozen, so the hash — not
/// the type system — is what detects tampering with the graph. That is also
/// what makes a running run auditable: the run pins a version id, and the hash
/// proves which graph that id meant at the time.
/// </para>
/// </remarks>
public sealed record WorkflowVersion(
    string VersionId,
    string DefinitionId,
    int Version,
    string SchemaVersion,
    IReadOnlyList<WorkflowNode> Nodes,
    string ContentHash);

/// <summary>
/// Canonical hashing of a workflow graph. Collections inside immutable records
/// are not themselves frozen, so publishing recomputes this and refuses a
/// mismatch instead of trusting whatever string arrived — "the hash, not the
/// type system, is what detects tampering with the graph" (doc 151 §4.1).
/// </summary>
public static class WorkflowGraph
{
    /// <summary>Stable digest of the graph; node order does not affect it.</summary>
    public static string ComputeContentHash(IReadOnlyList<WorkflowNode> nodes)
    {
        ArgumentNullException.ThrowIfNull(nodes);

        // Do not concatenate caller-controlled strings with delimiters here.
        // For example, capability="a|b", input="c" used to hash exactly like
        // capability="a", input="b|c".  A canonical JSON token stream keeps
        // field boundaries unambiguous while retaining deterministic ordering.
        using var canonical = new System.IO.MemoryStream();
        using (var writer = new System.Text.Json.Utf8JsonWriter(canonical))
        {
            writer.WriteStartArray();
            foreach (var node in nodes.OrderBy(n => n.NodeId, StringComparer.Ordinal))
            {
                writer.WriteStartObject();
                writer.WriteString("nodeId", node.NodeId);
                writer.WriteString("stageType", node.StageType.ToString());
                writer.WriteString("requiredCapability", node.RequiredCapability);
                writer.WriteString("inputContract", node.InputContract);
                writer.WriteString("outputContract", node.OutputContract);
                writer.WriteStartArray("allowedTransitions");
                foreach (var transition in node.AllowedTransitions.OrderBy(t => t))
                {
                    writer.WriteStringValue(transition.ToString());
                }
                writer.WriteEndArray();
                writer.WriteString("retryPolicyRef", node.RetryPolicyRef);
                writer.WriteString("policyRequirements", node.PolicyRequirements);
                writer.WriteNumber("timeoutSeconds", node.Budget.TimeoutSeconds);
                writer.WriteNumber("leaseSeconds", node.Budget.LeaseSeconds);
                writer.WriteBoolean("handoffRequired", node.HandoffRequired);
                writer.WriteEndObject();
            }
            writer.WriteEndArray();
        }

        var hash = System.Security.Cryptography.SHA256.HashData(canonical.ToArray());
        return "sha256:" + Convert.ToHexString(hash).ToLowerInvariant();
    }

    /// <summary>
    /// Deep copy into read-only collections. Records freeze their own
    /// membership but a caller-held List inside the graph (the Nodes list
    /// itself or any AllowedTransitions) stays mutable through it, and an
    /// array copy would still allow element replacement via IList's indexer.
    /// Publishing and restoring must both pass through this so the released
    /// graph cannot change while its ContentHash keeps claiming otherwise
    /// (doc 151 §12 invariant 1).
    /// </summary>
    public static WorkflowVersion Freeze(WorkflowVersion version) =>
        version with
        {
            Nodes = FreezeNodes(version.Nodes),
        };

    private static IReadOnlyList<WorkflowNode> FreezeNodes(IReadOnlyList<WorkflowNode> nodes)
    {
        var copied = nodes
            .Select(n => n with { AllowedTransitions = new System.Collections.ObjectModel.ReadOnlyCollection<StageType>(n.AllowedTransitions.ToArray()) })
            .ToArray();

        return new System.Collections.ObjectModel.ReadOnlyCollection<WorkflowNode>(copied);
    }
}

/// <summary>
/// Why a stage run exists beyond the first iteration (doc 151 §4.2).
/// </summary>
public static class StageRunReasons
{
    /// <summary>
    /// A prior review asked for changes. This drives a new development StageRun
    /// at a higher iteration rather than a "fix" stage.
    /// </summary>
    public const string ChangesRequested = "changes_requested";
}
