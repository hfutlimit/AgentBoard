// SPDX-License-Identifier: MIT
namespace AgentBoard.Contracts;

/// <summary>Operator-configured work capabilities, not provider names or Task types.</summary>
public static class WorkerWorkKinds
{
    public const string Proposal = "proposal";
    public const string Design = "design";
    public const string DesignReview = "design_review";
    public const string Dev = "dev";
    public const string DevReview = "dev_review";
    public const string Qa = "qa";
    public const string QaReview = "qa_review";

    public static IReadOnlyList<string> All { get; } = Array.AsReadOnly(new[]
        { Proposal, Design, DesignReview, Dev, DevReview, Qa, QaReview });

    public static string ForTask(string taskType, bool review = false) => (taskType, review) switch
    {
        ("design", false) => Design, ("design", true) => DesignReview,
        ("dev" or "bug", false) => Dev, ("dev" or "bug", true) => DevReview,
        ("qa", false) => Qa, ("qa", true) => QaReview,
        _ => throw new ArgumentException($"Unsupported task type: {taskType}", nameof(taskType)),
    };

    public static string Queue(int projectId, string kind)
    {
        if (projectId <= 0 || !All.Contains(kind, StringComparer.Ordinal))
            throw new ArgumentException("Explicit project and one of the seven work kinds are required");
        // All eligible workers share this queue. Never append worker/provider identity.
        return $"agentboard.work.v2.project.{projectId}.{kind}";
    }

    public const string Exchange = "agentboard.work.v2";
}
