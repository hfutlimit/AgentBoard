using AgentBoard.Node.Process;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace AgentBoard.Node.Agents;

/// <summary>
/// 千问办公 (Qwen) adapter. AgentType = "qwen".
///
/// The worker has no native 千问办公 headless CLI to spawn, so — exactly like
/// the WorkBuddy/Codex slots in <c>appsettings.Local.json</c> — the operator
/// points <c>Agents:Qwen:Command</c> at a Python invoker
/// (<c>scripts/qwen_invoker.py</c>) that speaks the Worker stdin→stdout
/// decision-JSON protocol against an OpenAI-compatible Qwen endpoint
/// (model <c>qwen3.8-flash</c>).
///
/// "完全访问" (full / unattended access): the invoker is a single-shot
/// completion with no interactive tool-approval gate — the prompt is the only
/// constraint — so nothing blocks the worker mid-run. This mirrors the
/// MiniMax/Codex unattended posture documented in appsettings.Production.json
/// rather than adding a new bypass flag to a desktop app.
/// </summary>
public sealed class QwenAdapter : IAgentAdapter
{
    private readonly IProcessExecutor _process;
    private readonly AgentsOptions _agents;
    private readonly AgentBoardOptions _agentboard;
    private readonly ILogger<QwenAdapter> _log;

    public QwenAdapter(
        IProcessExecutor process,
        IOptions<AgentsOptions> agents,
        IOptions<AgentBoardOptions> agentboard,
        ILogger<QwenAdapter> log)
    {
        _process = process;
        _agents = agents.Value;
        _agentboard = agentboard.Value;
        _log = log;
    }

    public string AgentType => "qwen";

    public Task<AgentExecutionResult> ExecuteAsync(ExecutionContext context, CancellationToken ct)
    {
        var opts = _agents.Qwen;
        var prompt = SharedAdapterHelpers.BuildWorkloadPrompt(
            agentName: "千问办公 (Qwen, qwen3.8-flash)",
            context: context);
        // The Command is expected to be an absolute python.exe path with the
        // invoker script as its argument; fall back to the bare "python" name
        // only when the operator left Arguments/Command to system defaults.
        var arguments = opts.Arguments is { Length: > 0 }
            ? opts.Arguments
            : Array.Empty<string>();
        var resolved = CliLocator.LocateGeneric("qwen", opts, _log);
        var env = new Dictionary<string, string?>(StringComparer.OrdinalIgnoreCase);
        foreach (var (k, v) in resolved.ExtraEnv) env[k] = v;

        // QWEN_* config the invoker reads. Model + API key are operator-provided
        // via the worker environment (never committed); we forward them so the
        // isolated child process (ProcessExecutor clears the parent env) sees
        // them. QWEN_MODEL defaults to qwen3.8-flash.
        foreach (var name in new[] { "QWEN_API_KEY", "QWEN_BASE_URL", "QWEN_MODEL", "QWEN_TIMEOUT" })
        {
            var value = System.Environment.GetEnvironmentVariable(name);
            if (!string.IsNullOrWhiteSpace(value)) env[name] = value;
        }
        if (!env.ContainsKey("QWEN_MODEL") || string.IsNullOrWhiteSpace(env["QWEN_MODEL"]))
            env["QWEN_MODEL"] = "qwen3.8-flash";
        if (!string.IsNullOrWhiteSpace(opts.ApiKeyEnv))
        {
            var value = System.Environment.GetEnvironmentVariable(opts.ApiKeyEnv);
            if (!string.IsNullOrWhiteSpace(value)) env[opts.ApiKeyEnv] = value;
        }

        // P0-1: forward AgentBoard MCP identity so the invoker's tool calls run
        // as the same service account the worker registered under.
        SharedAdapterHelpers.ApplyAgentBoardIdentity(
            env, opts.AgentBoardToken, _agentboard.StartupToken, _agentboard.ServerUrl);

        var spec = new ProcessSpec
        {
            Executable = resolved.Executable,
            WorkingDirectory = opts.WorkingDirectory,
            Arguments = arguments,
            StdinPayload = prompt,
            Environment = env,
            Timeout = TimeSpan.FromMinutes(Math.Max(1, opts.TimeoutMinutes)),
            MaxOutputBytes = opts.MaxCapturedOutputChars,
            AgentType = AgentType,
        };
        return SharedAdapterHelpers.RunAndParseAsync(_process, spec, ct);
    }
}
