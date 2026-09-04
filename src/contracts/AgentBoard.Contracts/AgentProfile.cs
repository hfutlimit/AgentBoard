// SPDX-License-Identifier: MIT
namespace AgentBoard.Contracts;

/// <summary>
/// The Operator-facing autonomy presets (doc 150 PR-005).
/// </summary>
/// <remarks>
/// These are a UX convenience, explicitly not a security boundary. doc 150
/// PR-005: "Review、Developer、Full 是面向 Operator 的 UX preset，不是安全边界。
/// preset 必须编译成带版本号的 PolicyRevision." A preset is the input to the
/// policy compiler; the thing that is enforced is the compiled revision, so no
/// runtime decision may read a preset directly.
/// </remarks>
public enum AutonomyPreset
{
    Review,
    Developer,
    Full,
}

/// <summary>
/// An immutable compiled policy (doc 151 §5.3).
/// </summary>
/// <param name="Id">Revision identifier, e.g. <c>policy-rev-17</c>.</param>
/// <param name="ContentHash">Digest of the compiled rules, used to detect drift.</param>
/// <param name="CompiledFromPreset">The preset it was compiled from, for audit only.</param>
/// <remarks>
/// Positional records are immutable, which is the point: doc 151 §5.3 requires
/// an immutable revision, and doc 151 §11 forbids a durable record from being
/// silently reinterpreted by whatever code version is running. A revision that
/// could be edited in place would make "what policy governed this decision"
/// unanswerable after the fact.
/// </remarks>
public sealed record PolicyRevision(string Id, string ContentHash, AutonomyPreset? CompiledFromPreset = null);

/// <summary>
/// The provider-neutral description of who does what (doc 151 §5.1, doc 150
/// PR-003).
/// </summary>
/// <remarks>
/// <para>
/// doc 151 §3 assigns ownership as "Server metadata + Node local runtime view"
/// and permits only "non-secret profile and policy reference" to cross the
/// boundary. In practice this type is the Server-visible projection: the Node
/// may hold more (the actual prompt files, the resolved executable path, the
/// credential), but none of that is part of this payload.
/// </para>
/// <para>
/// <see cref="LocalPromptPolicyRef"/> is a <em>reference</em> such as
/// <c>local://prompt-policy/dev-v1</c>, not the prompt text. That distinction
/// is what lets the Server know a prompt policy exists and which version
/// applies, without ever holding the prompt itself (doc 150 PR-015).
/// </para>
/// </remarks>
public sealed record AgentProfile
{
    public string AgentId { get; init; } = string.Empty;

    /// <summary>Free-form role label, e.g. <c>developer</c>.</summary>
    public string Role { get; init; } = string.Empty;

    /// <summary>
    /// Declared capabilities. At least one must be a <see cref="StageType"/>
    /// the agent can be assigned to.
    /// </summary>
    public IReadOnlyList<string> Capabilities { get; init; } = Array.Empty<string>();

    /// <summary>Reference to the provider catalog entry, e.g. <c>provider.codex</c>.</summary>
    public string ProviderRef { get; init; } = string.Empty;

    /// <summary>Reference to the transport to use, e.g. <c>transport.codex-cli</c>.</summary>
    public string TransportRef { get; init; } = string.Empty;

    /// <summary>Opaque JSON holding model and runtime options.</summary>
    public string RuntimeOptions { get; init; } = string.Empty;

    /// <summary>The compiled policy revision this agent runs under.</summary>
    public string PolicyRevisionId { get; init; } = string.Empty;

    /// <summary>Reference to the Node-local prompt policy, never the prompt text.</summary>
    public string LocalPromptPolicyRef { get; init; } = string.Empty;

    /// <summary>
    /// The preset this profile was configured with. Recorded for the Operator's
    /// benefit; enforcement uses <see cref="PolicyRevisionId"/>.
    /// </summary>
    public AutonomyPreset Preset { get; init; } = AutonomyPreset.Developer;

    /// <summary>The stage types this profile may be assigned to.</summary>
    public IEnumerable<StageType> AssignableStageTypes()
    {
        foreach (var capability in Capabilities)
        {
            if (Enum.TryParse<StageType>(capability, ignoreCase: true, out var stage))
            {
                yield return stage;
            }
        }
    }
}
