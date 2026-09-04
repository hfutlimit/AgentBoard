// SPDX-License-Identifier: MIT
namespace AgentBoard.Contracts;

/// <summary>
/// The explicit, verifiable context passed from one stage to the next
/// (doc 151 §7, doc 150 PR-010).
/// </summary>
/// <remarks>
/// <para>
/// The target stage may depend only on this object. doc 151 §7: "target stage
/// 只依赖 HandoffContext，不依赖 source provider session." Combined with
/// §5.2, which demotes ProviderSession to an optional adapter-internal context,
/// this is what makes a cross-provider handoff legal: the next stage is
/// reconstructed from task context, artifacts and evidence, never from a live
/// session left over from the previous one.
/// </para>
/// <para>
/// doc 151 §7 also requires handoff failure to be a diagnosable state rather
/// than a silently dropped context, which is why every field is explicit here
/// instead of being an open bag of prompt fragments.
/// </para>
/// </remarks>
public sealed record HandoffContext
{
    public string HandoffId { get; init; } = string.Empty;

    /// <summary>The stage run that produced the work being handed over.</summary>
    public string SourceStageRunId { get; init; } = string.Empty;

    /// <summary>The accepted Outcome the target stage is building on.</summary>
    public string SourceOutcomeId { get; init; } = string.Empty;

    /// <summary>Which stage type may consume this context.</summary>
    public StageType TargetStageType { get; init; }

    /// <summary>Opaque task context for the target stage.</summary>
    public string TaskContext { get; init; } = string.Empty;

    public IReadOnlyList<ArtifactReference> ArtifactReferences { get; init; } =
        Array.Empty<ArtifactReference>();

    public WorkspaceReference? Workspace { get; init; }

    public string? CommitOrVersion { get; init; }

    public IReadOnlyList<string> TestEvidence { get; init; } = Array.Empty<string>();

    public IReadOnlyList<string> ReviewFindings { get; init; } = Array.Empty<string>();

    /// <summary>Contract version of this context, e.g. <c>handoff.v1</c>.</summary>
    public string ContextVersion { get; init; } = string.Empty;

    /// <summary>Capabilities the receiving agent must declare to be assigned.</summary>
    public IReadOnlyList<string> RequiredCapabilities { get; init; } = Array.Empty<string>();
}
