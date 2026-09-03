using System.Text.Json;
using AgentBoard.Node.Process;

namespace AgentBoard.Node.Agents;

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
        //   task    → 按 task_type 分语义（P0-2，2026-09-01 review）：
        //              design → 只产出设计不改代码；qa → 只验证不改实现；
        //              dev/bug/legacy → 实现 + 测试 + commit
        //   review  → 评审，看 task + 评论，approve / reject
        //   rework  → 修 review 反馈，拉 review comment，修复后重 submit
        //   ticket  → 把 converged proposal 拆成 Story + Task DAG
        //   other   → 走 proposal 兼容路径（proposal.clarify 等老事件）
        var reworkScopeNote = ReworkScopeNote(context.TaskType);
        var workloadBlock = context.WorkloadType switch
        {
            WorkloadTypes.Task => BuildTaskPrompt(context),
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
                {reworkScopeNote}
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
                Auth check is delegated to the server: the claim endpoint does NOT verify proposal.author_id, so you can safely call proposal_ask / proposal_finalize regardless of who created the proposal. Do NOT pre-flight check authorship before calling MCP — the server returns 4xx only when the action is genuinely illegal.
                This message is the agent's only signal to advance the proposal. If you exit without calling proposal_ask or proposal_finalize, the proposal will stay in 'analyzing' forever and the worker will keep re-dispatching it.
                """,
        };

        return $"""
            You are the AgentBoard worker running on {agentName}. Use your configured AgentBoard MCP only; do not access AgentBoard databases directly.
            {workloadBlock}
            Unattended mode: do not make destructive local changes unless the workload explicitly asks and MCP confirms scope.
            {trace}
            """;
    }

    /// <summary>
    /// P0-2（2026-09-01 review）：Task 分支按 task_type 分执行语义。
    /// routing 已经特化（design→workbuddy / dev,bug→codex / qa→workbuddy），
    /// 但 prompt 不分语义会让 Design 阶段的 agent 直接开始改代码。
    /// task_type 为空（legacy 消息）→ 默认 implementation 语义。
    /// </summary>
    private static string BuildTaskPrompt(ExecutionContext context)
    {
        var taskType = (context.TaskType ?? "").Trim().ToLowerInvariant();
        return taskType switch
        {
            "design" => $"""
                You are handling a Design task (workload_type=task, task_type=design).
                1. Read the task {context.WorkloadId} through MCP: title, description, spec, dependencies, parent story.
                2. Analyze the requirements and inspect the relevant parts of the codebase (read-only).
                3. Produce the implementation design: approach, affected files/modules, edge cases, and a test plan. Persist the design on the task through MCP (update the spec/description or post a comment).
                4. When the design is complete, submit the task for review through MCP (status: in_review).
                Do NOT write implementation code and do NOT commit code changes — this is a design-only task. There is no Q&A history to reconstruct.
                """,
            "qa" => $"""
                You are handling a QA task (workload_type=task, task_type=qa).
                1. Read the task {context.WorkloadId} through MCP: title, description, spec, acceptance criteria, dependencies, parent story.
                2. Verify the implementation: run the relevant tests, inspect the acceptance criteria against the actual behavior and the change diff.
                3. Record the QA verdict with evidence (pass/fail per criterion) on the task through MCP (comment or spec update).
                4. When verification is complete, submit the QA result for review through MCP (status: in_review).
                Do NOT modify the implementation and do NOT commit fixes unless the task explicitly requires it — this is a verification-only task. There is no Q&A history to reconstruct.
                """,
            _ => $"""
                You are handling a Task implementation (workload_type=task, task_type={(taskType is "" ? "dev" : taskType)}).
                1. Read the task {context.WorkloadId} through MCP: title, description, spec, dependencies, parent story.
                2. Implement the task: read code, make changes, run relevant tests, commit.
                3. When complete, submit the task for review through MCP (status: in_review).
                Do NOT treat this as a proposal — this is a Task. There is no Q&A history to reconstruct.
                """,
        };
    }

    /// <summary>
    /// P0-2：rework 时同 task_type 的边界提醒（design 返工只改设计，
    /// qa 返工只改验证结论），防止 rework prompt 的 "Commit" 一刀切。
    /// </summary>
    private static string ReworkScopeNote(string? taskType) =>
        (taskType ?? "").Trim().ToLowerInvariant() switch
        {
            "design" => "This is a design rework: refine the design/spec only; do NOT write implementation code.",
            "qa" => "This is a QA rework: re-verify and correct the QA report; do NOT modify the implementation.",
            _ => "",
        };

    /// <summary>
    /// P0-1（2026-09-01 review）：把 Worker 的 AgentBoard 身份注入 CLI
    /// 子进程环境，保证「注册身份 == 执行身份 == MCP API 身份」。
    /// ProcessExecutor 会清空父进程环境（Sprint 5 隔离），AgentBoard MCP
    /// server 实际读取的环境变量是：
    ///   AGENTBOARD_MCP_TOKEN —— FastAPI Bearer token。per-agent token
    ///       优先；为空时回退 StartupToken（与 Worker startup
    ///       registration 的 fallback 语义一致）。
    ///   AGENTBOARD_API_URL   —— FastAPI 地址（MCP 默认
    ///       http://127.0.0.1:58124）。
    /// CLI（codex/workbuddy/minimax）spawn MCP server 子进程时继承这
    /// 两个变量；两个都拿不到才不注入（dev 环境无鉴权可跑）。
    /// </summary>
    public static void ApplyAgentBoardIdentity(
        Dictionary<string, string?> env,
        string? perAgentToken,
        string? startupToken,
        string? serverUrl)
    {
        var token = string.IsNullOrWhiteSpace(perAgentToken) ? startupToken : perAgentToken;
        if (!string.IsNullOrWhiteSpace(token))
            env["AGENTBOARD_MCP_TOKEN"] = token;
        if (!string.IsNullOrWhiteSpace(serverUrl))
            env["AGENTBOARD_API_URL"] = serverUrl;
    }
}
