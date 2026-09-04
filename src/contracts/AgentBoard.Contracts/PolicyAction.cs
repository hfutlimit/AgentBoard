// SPDX-License-Identifier: MIT
namespace AgentBoard.Contracts;

/// <summary>
/// The known action kinds a Policy Decision Point rules on (doc 151 §5.3).
/// </summary>
/// <remarks>
/// The kind is a string rather than an enum because providers keep introducing
/// new action shapes, and doc 151 §10 rule 8 requires the unknown case to be
/// handled explicitly: an action kind the policy does not recognise must be
/// representable so the PDP can deny it, not crash or fall through to a
/// default. An enum could only express the values known at compile time.
/// </remarks>
public static class PolicyActionKinds
{
    public const string ReadFile = "read_file";
    public const string WriteFile = "write_file";
    public const string ExecuteShell = "execute_shell";
    public const string NetworkEgress = "network_egress";
    public const string GitCommit = "git_commit";
    public const string GitPush = "git_push";
    public const string SpawnProviderProcess = "spawn_provider_process";
    public const string PublishArtifact = "publish_artifact";

    public static IReadOnlySet<string> Known { get; } = new HashSet<string>(StringComparer.Ordinal)
    {
        ReadFile,
        WriteFile,
        ExecuteShell,
        NetworkEgress,
        GitCommit,
        GitPush,
        SpawnProviderProcess,
        PublishArtifact,
    };

    public static bool IsKnown(string? kind) =>
        !string.IsNullOrWhiteSpace(kind) && Known.Contains(kind);
}

/// <summary>
/// The action a Node is about to take (doc 151 §5.3).
/// </summary>
/// <param name="Kind">One of <see cref="PolicyActionKinds"/>, or an unknown value to be denied.</param>
/// <param name="Resource">What the action targets: a path, a host, a command.</param>
public sealed record PolicyAction(string Kind, string Resource);

/// <summary>
/// Everything the PDP is required to consider before deciding
/// (doc 151 §5.3).
/// </summary>
/// <remarks>
/// doc 151 §5.3: "PDP 必须基于 action kind、resource、agent capability、
/// workflow/stage context、workspace、policy revision 和 approval state 决策。"
/// Every one of those inputs is a field here, so a decision cannot be reached
/// with part of the context missing.
/// </remarks>
public sealed record PolicyDecisionRequest(
    PolicyAction Action,
    string AgentId,
    IReadOnlyList<string> AgentCapabilities,
    StageType? Stage,
    string? WorkflowRunId,
    WorkspaceReference? Workspace,
    string PolicyRevisionId,
    bool ApprovalGranted);
