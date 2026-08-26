using AgentBoard.ProposalWorker.Process;
using Microsoft.Extensions.Options;

namespace AgentBoard.ProposalWorker.Agents;

/// <summary>
/// OpenAI Codex CLI adapter. The current Codex CLI accepts the prompt through
/// stdin when no positional prompt argument is supplied. JSONL event output is
/// enabled so the worker can retain a structured execution trace.
/// </summary>
public sealed class CodexAdapter : IAgentAdapter
{
    private readonly IProcessExecutor _process;
    private readonly AgentsOptions _agents;

    public CodexAdapter(IProcessExecutor process, IOptions<AgentsOptions> agents)
    {
        _process = process;
        _agents = agents.Value;
    }

    public string AgentType => "codex";

    public Task<AgentExecutionResult> ExecuteAsync(ExecutionContext context, CancellationToken ct)
    {
        var opts = _agents.Codex;
        var prompt = BuildPrompt(context);
        var spec = new ProcessSpec
        {
            Executable = opts.Command,
            WorkingDirectory = opts.WorkingDirectory,
            // Current Codex CLI: codex exec --json, prompt via stdin.
            // There is no --prompt option in the installed CLI.
            Arguments = new[] { "exec", "--json" },
            StdinPayload = prompt,
            Environment = BuildEnvironment(opts),
            Timeout = TimeSpan.FromMinutes(Math.Max(1, opts.TimeoutMinutes)),
            MaxOutputBytes = opts.MaxCapturedOutputChars,
            AgentType = AgentType,
        };
        return SharedAdapterHelpers.RunAndParseAsync(_process, spec, ct);
    }

    private static Dictionary<string, string?> BuildEnvironment(AgentOptions opts)
    {
        // ProcessExecutor intentionally starts from an empty environment. Keep
        // the allow-list explicit: Codex needs the executable search path and
        // the user/config locations used by its local login and MCP settings.
        var env = new Dictionary<string, string?>(StringComparer.OrdinalIgnoreCase);
        foreach (var name in new[]
        {
            "PATH", "USERPROFILE", "CODEX_HOME", "LOCALAPPDATA", "APPDATA",
            "HOME", "TEMP", "TMP"
        })
        {
            var value = System.Environment.GetEnvironmentVariable(name);
            if (!string.IsNullOrWhiteSpace(value)) env[name] = value;
        }

        // API-key mode is optional; ChatGPT-login mode uses CODEX_HOME instead.
        if (!string.IsNullOrWhiteSpace(opts.ApiKeyEnv))
        {
            var value = System.Environment.GetEnvironmentVariable(opts.ApiKeyEnv);
            if (!string.IsNullOrWhiteSpace(value)) env[opts.ApiKeyEnv] = value;
        }
        return env;
    }

    private string BuildPrompt(ExecutionContext context) => $"""
        You are the AgentBoard worker running on OpenAI Codex CLI. Use your configured AgentBoard MCP only.
        Handle proposal {context.WorkloadId} (round {context.Round}) on worker '{context.ExecutionKey}'.
        Reconstruct the proposal's complete question-answer history through MCP, then decide the next action.
        If you need clarification, write concrete open questions through MCP. If converged, write the converged proposal. If appropriate, record failure.
        Unattended mode: do not make destructive local changes unless the proposal explicitly asks and MCP confirms scope.
        """;
}
