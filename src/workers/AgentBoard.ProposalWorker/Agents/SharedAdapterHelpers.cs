using System.Text.Json;
using AgentBoard.ProposalWorker.Process;

namespace AgentBoard.ProposalWorker.Agents;

/// <summary>
/// Shared helper for adapters that don't need the workbuddy-specific
/// stdin-only behavior. Runs the process and parses the last JSON object
/// from the tail, identical to the legacy WorkBuddyRunner parsing logic.
/// </summary>
internal static class SharedAdapterHelpers
{
    public static async Task<AgentExecutionResult> RunAndParseAsync(IProcessExecutor process, ProcessSpec spec, CancellationToken ct)
    {
        var result = await process.ExecuteAsync(spec, ct);
        var output = result.RedactedOutput ?? "";
        return new AgentExecutionResult(
            Success: result.ExitCode == 0 && !result.TimedOut && !result.Cancelled,
            OutputJson: TryExtractLastJson(output),
            ErrorMessage: result.Cancelled ? "cancelled"
                : result.TimedOut ? "timeout"
                : result.ExitCode == 0 ? null : $"exit {result.ExitCode}: {result.StderrTail}",
            ExitCode: result.ExitCode,
            Duration: result.Duration,
            TimedOut: result.TimedOut,
            Cancelled: result.Cancelled);
    }

    private static string? TryExtractLastJson(string text)
    {
        for (var i = text.Length - 1; i >= 0; i--)
        {
            if (text[i] != '{') continue;
            var depth = 0;
            for (var j = i; j < text.Length; j++)
            {
                if (text[j] == '{') depth++;
                else if (text[j] == '}')
                {
                    depth--;
                    if (depth == 0)
                    {
                        var slice = text[i..(j + 1)];
                        try { using var _ = JsonDocument.Parse(slice); return slice; }
                        catch { break; }
                    }
                }
            }
        }
        return null;
    }

    /// <summary>
    /// PR-3 (Generic AgentWorker): workload-aware prompt builder.
    /// Replaces the legacy "Handle proposal {id}" hard-coded prompt that
    /// made Codex / WorkBuddy / MiniMax treat every workload as a proposal
    /// (PR-2 review P0-4). The dispatch decision (which agent runs this) is
    /// upstream of this builder — we already know who we are. What we need
    /// to know is *what* we are doing: a task implementation, a review,
    /// a rework, or a ticket materialization. The CLI agent must phrase
    /// its actions accordingly when calling AgentBoard MCP.
    /// </summary>
    public static string BuildWorkloadPrompt(
        string agentName, ExecutionContext context, string? correlationId = null)
    {
        // correlation_id is the trace chain id (PR-2 字段). Optional
        // —— 老 dispatcher 不传时为空，前向兼容。
        var trace = string.IsNullOrWhiteSpace(correlationId)
            ? ""
            : $" Trace id: {correlationId}. When calling MCP, echo this trace id so the chain is auditable.";

        // workload 主体 + MCP 指引按 workload_type 分支（核心 P0-4 修复）：
        //   task    → 实现（dev/bug），MCP 拉 task context，完成后 submit_for_review
        //   review  → 评审，看 task + 评论，approve / reject
        //   rework  → 修 review 反馈，拉 review comment，修复后重 submit
        //   ticket  → 把 converged proposal 拆成 Story + Task DAG
        //   other   → 走 proposal 兼容路径（proposal.clarify 等老事件）
        var workloadBlock = context.WorkloadType switch
        {
            WorkloadTypes.Task => $"""
                You are handling a Task implementation (workload_type=task).
                1. Read the task {context.WorkloadId} through MCP: title, description, spec, dependencies, parent story.
                2. Implement the task: read code, make changes, run relevant tests, commit.
                3. When complete, submit the task for review through MCP (status: in_review).
                Do NOT treat this as a proposal — this is a Task. There is no Q&A history to reconstruct.
                """,
            WorkloadTypes.Review => $"""
                You are handling a Task review (workload_type=review).
                1. Read the task {context.WorkloadId} and the implementation commit/diff through MCP.
                2. Read the review rubric (project settings → review requirements).
                3. Decide: approve (cast approve vote with concrete reasoning) or reject (cast reject with the exact issues to fix).
                Do NOT treat this as a proposal — this is a review. No new questions to ask the user.
                """,
            WorkloadTypes.Rework => $"""
                You are handling a Task rework (workload_type=rework, round {context.Round}).
                1. Read the task {context.WorkloadId} and the most recent review comment (which rejected the prior round) through MCP.
                2. Address the feedback concretely. Re-run tests. Commit.
                3. Re-submit for review through MCP.
                Do NOT treat this as a proposal — this is a rework loop. The review comment is your source of truth, not Q&A history.
                """,
            WorkloadTypes.Ticket => $"""
                You are materializing a converged proposal into a Story + Task DAG (workload_type=ticket).
                1. Read the converged proposal {context.WorkloadId} through MCP.
                2. Decompose into one Story and the appropriate Task set (design, implementation, QA, ...).
                3. Persist through MCP. The Story will become the user's working surface.
                Do NOT treat this as an active proposal — this proposal is already converged.
                """,
            // 非 task / review / rework / ticket 视为 proposal 兼容路径
            // （proposal.clarify / proposal.answered 等老事件仍按原方式处理）
            _ => $"""
                Handle proposal {context.WorkloadId} (round {context.Round}) on worker '{context.ExecutionKey}'.
                Reconstruct the proposal's complete question-answer history through MCP, then decide the next action.
                If you need clarification, write concrete open questions through MCP. If converged, write the converged proposal. If appropriate, record failure.
                """,
        };

        return $"""
            You are the AgentBoard worker running on {agentName}. Use your configured AgentBoard MCP only; do not access AgentBoard databases directly.
            {workloadBlock}
            Unattended mode: do not make destructive local changes unless the workload explicitly asks and MCP confirms scope.
            {trace}
            """;
    }
}
