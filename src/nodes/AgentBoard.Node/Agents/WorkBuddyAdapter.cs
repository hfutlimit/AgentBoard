using System.Text.Json;
using AgentBoard.Node.Process;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace AgentBoard.Node.Agents;

/// <summary>
/// Sprint 4. Migrated from the original WorkBuddyRunner. stdin-driven,
/// stdout last-line-JSON-decision. Behavior matches the previous
/// implementation byte-for-byte so existing e2e tests stay green.
/// Resolves the codebuddy CLI via <see cref="CliLocator"/>.
/// </summary>
public sealed class WorkBuddyAdapter : IAgentAdapter
{
    private readonly IProcessExecutor _process;
    private readonly AgentsOptions _agents;
    private readonly AgentBoardOptions _agentboard;
    private readonly ILogger<WorkBuddyAdapter> _log;

    public WorkBuddyAdapter(
        IProcessExecutor process,
        IOptions<AgentsOptions> agents,
        IOptions<AgentBoardOptions> agentboard,
        ILogger<WorkBuddyAdapter> log)
    {
        _process = process;
        _agents = agents.Value;
        _agentboard = agentboard.Value;
        _log = log;
    }

    public string AgentType => "workbuddy";

    public Task<AgentExecutionResult> ExecuteAsync(ExecutionContext context, CancellationToken ct)
    {
        var opts = _agents.WorkBuddy;
        var resolved = CliLocator.LocateCodebuddy(opts, _log);
        var env = new Dictionary<string, string?>(StringComparer.OrdinalIgnoreCase);
        foreach (var (k, v) in resolved.ExtraEnv) env[k] = v;
        // P0-1（2026-09-01 review）：CLI 子进程里 MCP server 读
        // AGENTBOARD_MCP_TOKEN / AGENTBOARD_API_URL；不注入的话
        // 注册身份 ≠ MCP API 身份，submit-review 会被
        // task.assignee_id 校验拒绝。
        SharedAdapterHelpers.ApplyAgentBoardIdentity(
            env, opts.AgentBoardToken, _agentboard.StartupToken, _agentboard.ServerUrl);
        // PR-3: workload-aware prompt（不再是硬编码 "Handle proposal"）。
        var arguments = resolved.PrefixArguments.Concat(opts.Arguments).ToArray();
        var spec = new ProcessSpec
        {
            Executable = resolved.Executable,
            Arguments = arguments,
            WorkingDirectory = opts.WorkingDirectory,
            StdinPayload = SharedAdapterHelpers.BuildWorkloadPrompt(
                agentName: "the WorkBuddy CLI (codebuddy)",
                context: context),
            Timeout = TimeSpan.FromMinutes(Math.Max(1, opts.TimeoutMinutes)),
            MaxOutputBytes = opts.MaxCapturedOutputChars,
            Environment = env,
            AgentType = AgentType,
        };
        // 2026-09-02 (operator verify): log the exact command we are
        // about to spawn so post-mortem on 124 can confirm whether
        // the new template (codebuddy CLI) or the old misrouted
        // (python scripts/minimax_invoker.py) is actually in effect.
        _log.LogInformation(
            "WorkBuddyAdapter.Execute: workload={Workload} id={Id} " +
            "cmd='{Cmd}' args=[{Args}] model-hint={Model}",
            context.WorkloadType, context.WorkloadId,
            spec.Executable, string.Join(" ", spec.Arguments),
            opts.Arguments.Length > 0 ? opts.Arguments[^1] : "(none)");
        return ExecuteSpecAsync(context, spec, ct);
    }

    private async Task<AgentExecutionResult> ExecuteSpecAsync(ExecutionContext context, ProcessSpec spec, CancellationToken ct)
    {
        var result = await _process.ExecuteAsync(spec, ct);
        var output = result.RedactedOutput ?? "";
        // 2026-09-02: tail the last 400 chars of stdout so a reviewer
        // can see what codebuddy actually returned (the 'ask' decision
        // body is normally the last line of stdout). When a run
        // fails the failure reason + stderr tail are also logged.
        var tail = output.Length > 400 ? "…" + output[^400..] : output;
        _log.LogInformation(
            "WorkBuddyAdapter.Execute: workload={Workload} id={Id} exit={Exit} " +
            "duration={Dur}ms timed-out={To} cancelled={Can} success={Ok} stdout-tail={Tail}",
            context.WorkloadType, context.WorkloadId, result.ExitCode,
            (long)result.Duration.TotalMilliseconds, result.TimedOut,
            result.Cancelled, result.ExitCode == 0 && !result.TimedOut && !result.Cancelled,
            tail);
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
        // Walk from the end, looking for a balanced { ... } that parses.
        for (var i = text.Length - 1; i >= 0; i--)
        {
            if (text[i] != '{') continue;
            var depth = 0; var ok = false;
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
                        catch { ok = false; break; }
                    }
                }
            }
            if (ok) break;
        }
        return null;
    }
}
