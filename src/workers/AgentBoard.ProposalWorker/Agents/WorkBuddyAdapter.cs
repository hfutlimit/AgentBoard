using System.Text.Json;
using AgentBoard.ProposalWorker.Process;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace AgentBoard.ProposalWorker.Agents;

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
    private readonly WorkerOptions _worker;
    private readonly ILogger<WorkBuddyAdapter> _log;

    public WorkBuddyAdapter(
        IProcessExecutor process,
        IOptions<AgentsOptions> agents,
        IOptions<WorkerOptions> worker,
        ILogger<WorkBuddyAdapter> log)
    {
        _process = process;
        _agents = agents.Value;
        _worker = worker.Value;
        _log = log;
    }

    public string AgentType => "workbuddy";

    public Task<AgentExecutionResult> ExecuteAsync(ExecutionContext context, CancellationToken ct)
    {
        var opts = _agents.WorkBuddy;
        var resolved = CliLocator.LocateCodebuddy(opts, _log);
        var env = new Dictionary<string, string?>(StringComparer.OrdinalIgnoreCase);
        foreach (var (k, v) in resolved.ExtraEnv) env[k] = v;
        var spec = new ProcessSpec
        {
            Executable = resolved.Executable,
            Arguments = opts.Arguments,
            WorkingDirectory = opts.WorkingDirectory,
            StdinPayload = BuildPrompt(context),
            Timeout = TimeSpan.FromMinutes(Math.Max(1, opts.TimeoutMinutes)),
            MaxOutputBytes = opts.MaxCapturedOutputChars,
            Environment = env,
            AgentType = AgentType,
        };
        return ExecuteSpecAsync(context, spec, ct);
    }

    private async Task<AgentExecutionResult> ExecuteSpecAsync(ExecutionContext context, ProcessSpec spec, CancellationToken ct)
    {
        var result = await _process.ExecuteAsync(spec, ct);
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

    private string BuildPrompt(ExecutionContext context) => $"""
        You are the AgentBoard proposal worker. Use your already configured AgentBoard MCP only; do not access AgentBoard databases directly.
        Handle proposal {context.WorkloadId}, round {context.Round}, on worker '{_worker.Id}'.
        Claim/read the proposal through MCP, reconstruct its complete question-answer history, and determine the next action.
        If clarification is needed, write concrete open questions through MCP. If it is converged, write the converged proposal through MCP. Record failures through MCP when appropriate.
        This is an unattended worker: make no destructive local changes unless the proposal explicitly asks for them and MCP confirms the project scope.
        """;

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
