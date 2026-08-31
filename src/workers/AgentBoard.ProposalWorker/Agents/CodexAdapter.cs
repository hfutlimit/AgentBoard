using AgentBoard.ProposalWorker.Process;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace AgentBoard.ProposalWorker.Agents;

/// <summary>
/// OpenAI Codex CLI adapter. The current Codex CLI accepts the prompt through
/// stdin when no positional prompt argument is supplied. JSONL event output is
/// enabled so the worker can retain a structured execution trace. Resolves
/// the codex executable via <see cref="CliLocator"/>.
/// </summary>
public sealed class CodexAdapter : IAgentAdapter
{
    private readonly IProcessExecutor _process;
    private readonly AgentsOptions _agents;
    private readonly ILogger<CodexAdapter> _log;

    public CodexAdapter(
        IProcessExecutor process,
        IOptions<AgentsOptions> agents,
        ILogger<CodexAdapter> log)
    {
        _process = process;
        _agents = agents.Value;
        _log = log;
    }

    public string AgentType => "codex";

    public Task<AgentExecutionResult> ExecuteAsync(ExecutionContext context, CancellationToken ct)
    {
        var opts = _agents.Codex;
        // PR-3: workload-aware prompt（不再是硬编码 "Handle proposal"）。
        // 透传 correlation_id（PR-2 字段）让 Codex 在 MCP 调用里能串 trace。
        var prompt = SharedAdapterHelpers.BuildWorkloadPrompt(
            agentName: "OpenAI Codex CLI",
            context: context);
        // Default Codex CLI invocation: `codex exec --json` (prompt via stdin;
        // there is no --prompt option in the installed CLI). Operators can
        // override via `Agents:Codex:Arguments` — production defaults to the
        // current CLI's unattended bypass flag so the worker can read/write
        // the project directory without per-tool approval prompts.
        var arguments = opts.Arguments is { Length: > 0 }
            ? opts.Arguments
            : new[] { "exec", "--json" };
        var resolved = CliLocator.LocateCodex(opts, _log);
        var spec = new ProcessSpec
        {
            Executable = resolved.Executable,
            WorkingDirectory = opts.WorkingDirectory,
            Arguments = arguments,
            StdinPayload = prompt,
            Environment = BuildEnvironment(opts, resolved.ExtraEnv),
            Timeout = TimeSpan.FromMinutes(Math.Max(1, opts.TimeoutMinutes)),
            MaxOutputBytes = opts.MaxCapturedOutputChars,
            AgentType = AgentType,
        };
        return SharedAdapterHelpers.RunAndParseAsync(_process, spec, ct);
    }

    private static Dictionary<string, string?> BuildEnvironment(
        AgentOptions opts,
        IReadOnlyDictionary<string, string> locatorEnv)
    {
        // ProcessExecutor intentionally starts from an empty environment. Start
        // with the locator's allow-list (PATH, USERPROFILE, ...) and layer
        // Codex-specific vars (CODEX_HOME) plus the optional API key.
        var env = new Dictionary<string, string?>(StringComparer.OrdinalIgnoreCase);
        foreach (var (k, v) in locatorEnv) env[k] = v;

        // Codex-specific: CODEX_HOME and HOME (POSIX shim) are read by the CLI
        // for its local login and config locations; not always present in
        // the locator's base allow-list.
        foreach (var name in new[] { "CODEX_HOME", "HOME" })
        {
            var value = System.Environment.GetEnvironmentVariable(name);
            if (!string.IsNullOrWhiteSpace(value) && !env.ContainsKey(name)) env[name] = value;
        }

        // API-key mode is optional; ChatGPT-login mode uses CODEX_HOME instead.
        if (!string.IsNullOrWhiteSpace(opts.ApiKeyEnv))
        {
            var value = System.Environment.GetEnvironmentVariable(opts.ApiKeyEnv);
            if (!string.IsNullOrWhiteSpace(value)) env[opts.ApiKeyEnv] = value;
        }
        return env;
    }
}
