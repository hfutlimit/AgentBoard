// SPDX-License-Identifier: MIT
using System.Security.Cryptography;
using System.Text;
using AgentBoard.Contracts;

namespace AgentBoard.Node.Durable;

/// <summary>
/// The Node's local compiled view of an immutable policy revision
/// (doc 151 §5.3). The presets are the operator-facing UX names from doc 150
/// PR-005 — explicitly NOT a security boundary; what enforces is the compiled,
/// versioned revision. The cross-boundary identity stays the A0
/// <see cref="AgentBoard.Contracts.PolicyRevision"/> record; this type pairs it
/// with the effective rules the PDP evaluates.
/// </summary>
public sealed class CompiledPolicy
{
    private CompiledPolicy(string revisionId, string presetName, IReadOnlyDictionary<string, PolicyDecision> rules)
    {
        RevisionId = revisionId;
        PresetName = presetName;
        Rules = rules;
    }

    /// <summary>The A0 reference value carrying this revision across boundaries.</summary>
    public PolicyRevision AsReference() => new(RevisionId, RevisionId);

    public string RevisionId { get; }

    public string PresetName { get; }

    public IReadOnlyDictionary<string, PolicyDecision> Rules { get; }

    public static CompiledPolicy Compile(string presetName, IReadOnlyDictionary<string, PolicyDecision> overrides)
    {
        var baseRules = presetName switch
        {
            PolicyPresets.Review => ReviewPreset(),
            PolicyPresets.Developer => DeveloperPreset(),
            PolicyPresets.Full => FullPreset(),
            _ => throw new ArgumentException($"unknown policy preset '{presetName}'"),
        };

        var rules = new Dictionary<string, PolicyDecision>(baseRules, StringComparer.Ordinal);
        foreach (var (kind, decision) in overrides)
        {
            if (!PolicyActionKinds.Known.Contains(kind))
            {
                // Overrides may not invent action kinds: presets compile, they
                // do not widen the vocabulary (doc 151 §11: unknowns deny).
                throw new ArgumentException($"override names unknown action kind '{kind}'");
            }

            rules[kind] = decision;
        }

        return new CompiledPolicy(ComputeRevisionId(presetName, rules), presetName, rules);
    }

    private static Dictionary<string, PolicyDecision> ReviewPreset() => new(StringComparer.Ordinal)
    {
        [PolicyActionKinds.ReadFile] = PolicyDecision.Allow,
        [PolicyActionKinds.SpawnProviderProcess] = PolicyDecision.Allow,
        [PolicyActionKinds.PublishArtifact] = PolicyDecision.Allow,
        [PolicyActionKinds.WriteFile] = PolicyDecision.Deny,
        [PolicyActionKinds.ExecuteShell] = PolicyDecision.Deny,
        [PolicyActionKinds.NetworkEgress] = PolicyDecision.RequireApproval,
        [PolicyActionKinds.GitCommit] = PolicyDecision.Deny,
        [PolicyActionKinds.GitPush] = PolicyDecision.Deny,
    };

    private static Dictionary<string, PolicyDecision> DeveloperPreset() => new(StringComparer.Ordinal)
    {
        [PolicyActionKinds.ReadFile] = PolicyDecision.Allow,
        [PolicyActionKinds.WriteFile] = PolicyDecision.Allow,
        [PolicyActionKinds.ExecuteShell] = PolicyDecision.RequireApproval,
        [PolicyActionKinds.SpawnProviderProcess] = PolicyDecision.Allow,
        [PolicyActionKinds.PublishArtifact] = PolicyDecision.Allow,
        [PolicyActionKinds.GitCommit] = PolicyDecision.RequireApproval,
        [PolicyActionKinds.GitPush] = PolicyDecision.Deny,
        [PolicyActionKinds.NetworkEgress] = PolicyDecision.RequireApproval,
    };

    private static Dictionary<string, PolicyDecision> FullPreset() => new(StringComparer.Ordinal)
    {
        [PolicyActionKinds.ReadFile] = PolicyDecision.Allow,
        [PolicyActionKinds.WriteFile] = PolicyDecision.Allow,
        [PolicyActionKinds.ExecuteShell] = PolicyDecision.Allow,
        [PolicyActionKinds.SpawnProviderProcess] = PolicyDecision.Allow,
        [PolicyActionKinds.PublishArtifact] = PolicyDecision.Allow,
        [PolicyActionKinds.GitCommit] = PolicyDecision.RequireApproval,
        [PolicyActionKinds.GitPush] = PolicyDecision.RequireApproval,
        [PolicyActionKinds.NetworkEgress] = PolicyDecision.RequireApproval,
    };

    /// <summary>
    /// Deterministic id over the compiled rules: the same preset + overrides
    /// always yields the same revision, so audit can pin exactly what was in
    /// force (doc 151 §11: PolicyRevision must be auditable and comparable).
    /// </summary>
    private static string ComputeRevisionId(string preset, IReadOnlyDictionary<string, PolicyDecision> rules)
    {
        var canonical = string.Concat(
            preset, "|",
            string.Join(";", rules.OrderBy(kv => kv.Key, StringComparer.Ordinal)
                .Select(kv => $"{kv.Key}={kv.Value}")));

        var hash = SHA256.HashData(Encoding.UTF8.GetBytes(canonical));
        return $"policy-rev-{Convert.ToHexString(hash[..8]).ToLowerInvariant()}";
    }
}

public static class PolicyPresets
{
    public const string Review = "review";
    public const string Developer = "developer";
    public const string Full = "full";
}

/// <summary>
/// The Node-local decision point: given a request, produce ALLOW / DENY /
/// REQUIRE_APPROVAL, honoring default-deny for unknown kinds and fail-fast for
/// approvals that cannot arrive (doc 150 PR-005, doc 151 §5.3).
/// </summary>
public sealed class PolicyDecisionPoint
{
    private readonly CompiledPolicy _revision;

    public PolicyDecisionPoint(CompiledPolicy revision) => _revision = revision;

    public IReadOnlyList<string> Violations { get; private set; } = Array.Empty<string>();

    public (PolicyDecision Decision, FailureCategory Failure) Decide(PolicyDecisionRequest request)
    {
        var errors = PolicyValidator.Validate(request);
        if (errors.Count > 0)
        {
            Violations = errors.Select(e => $"{e.Field} {e.Reason}").ToList();

            // A malformed request is a schema rejection, and it denies.
            return (PolicyDecision.Deny, FailureCategory.SchemaRejection);
        }

        Violations = Array.Empty<string>();

        if (PolicyValidator.DefaultDenyForUnknownKind(request) is { } denied)
        {
            return denied;
        }

        if (!string.Equals(request.PolicyRevisionId, _revision.RevisionId, StringComparison.Ordinal))
        {
            // The Node evaluates only under the revision it holds; acting on
            // a request pinned to another revision would reinterpret durable
            // decisions under whatever code is running now (doc 151 §11).
            return (PolicyDecision.Deny, FailureCategory.PolicyDenied);
        }

        if (!_revision.Rules.TryGetValue(request.Action.Kind, out var decision))
        {
            // Compiled presets cover the known kinds; a gap denies rather
            // than falling through to an implicit allow.
            return (PolicyDecision.Deny, FailureCategory.PolicyDenied);
        }

        if (decision != PolicyDecision.RequireApproval)
        {
            return (decision, decision == PolicyDecision.Allow ? FailureCategory.None : FailureCategory.PolicyDenied);
        }

        // REQUIRE_APPROVAL resolves through the A0 helper: granted becomes
        // Allow; with no channel it fails fast as ApprovalUnavailable, never
        // hangs (doc 151 §5.3).
        return PolicyValidator.ResolveApproval(request);
    }
}

public enum EnforcementOutcome { Executed, Denied, ApprovalRequired, HeldForApproval }

/// <summary>
/// The Node-local enforcement point: the only path through which a provider
/// invocation may happen. Deny never runs the action; RequireApproval either
/// hands off to an approval channel or fails fast, and an unattended runner
/// passes ApprovalGranted=false so the fail-fast path is the only one
/// (doc 150 PR-005: "所有高风险 action 在 provider 调用前经过 PEP").
/// </summary>
public sealed class PolicyEnforcementPoint
{
    private readonly PolicyDecisionPoint _pdp;
    private readonly Action<AuditLine>? _audit;

    public PolicyEnforcementPoint(PolicyDecisionPoint pdp, Action<AuditLine>? audit = null)
    {
        _pdp = pdp;
        _audit = audit;
    }

    public sealed record AuditLine(string Action, string Kind, string Resource, PolicyDecision Decision, FailureCategory Failure);

    public EnforcementResult<T> Execute<T>(PolicyDecisionRequest request, Func<T> action)
    {
        var (decision, failure) = _pdp.Decide(request);
        _audit?.Invoke(new AuditLine("policy.decision", request.Action.Kind, request.Action.Resource, decision, failure));

        switch (decision)
        {
            case PolicyDecision.Allow:
                return new EnforcementResult<T>(EnforcementOutcome.Executed, failure, action());

            case PolicyDecision.Deny:
                // The action delegate is never reached on a denial.
                return new EnforcementResult<T>(EnforcementOutcome.Denied, failure, default);

            default:
                // RequireApproval with a channel (the request already carries
                // ApprovalGranted=false): the runner parks the attempt until
                // an operator decides; there is no code path that proceeds
                // without the flag being true.
                return new EnforcementResult<T>(EnforcementOutcome.ApprovalRequired, failure, default);
        }
    }

    public sealed record EnforcementResult<T>(EnforcementOutcome Outcome, FailureCategory Failure, T? Value);
}

// ---------------------------------------------------------------------------
// Provider adapter seam (doc 151 §5.2): the minimal capability surface every
// provider adapter must expose. AgentBoard owns none of the credential
// lifecycle inside these calls — official login and secret storage belong to
// the provider (doc 150 PR-004).
// ---------------------------------------------------------------------------

public enum ProviderAuthState { Unknown, Ready, NeedsLogin, Expired, BrokenInstall }

public sealed record ProviderReadiness(ProviderAuthState Auth, string Detail, DateTimeOffset ProbedAt);

public sealed record AttemptStartContext(
    string AttemptId,
    string AssignmentId,
    long LeaseEpoch,
    string WorkspacePath,
    string PromptOrReference,
    IReadOnlyDictionary<string, string> RuntimeOptions);

public sealed record ProviderAttemptHandle(string AttemptId);

public sealed record ProviderAttemptOutcome(
    AttemptResultStatus Status,
    FailureCategory Failure,
    string Summary,
    IReadOnlyList<ArtifactReference> Artifacts,
    string? CommitOrVersion);

public interface IProviderAdapter
{
    string ProviderId { get; }

    ProviderReadiness CheckAuth();

    /// <summary>Optional official-login handoff; null when the provider has none.</summary>
    bool BeginOfficialLogin() => false;

    ProviderReadiness CheckReady();

    ProviderAttemptHandle StartAttempt(AttemptStartContext context);

    /// <summary>Yields local detail events as they happen; stored, not forwarded raw.</summary>
    IAsyncEnumerable<EventEnvelope> StreamLocalEvents(ProviderAttemptHandle handle, CancellationToken cancellation);

    void CancelAttempt(ProviderAttemptHandle handle);

    ProviderAttemptOutcome CollectAttemptResult(ProviderAttemptHandle handle);
}
