using AgentBoard.Node.Process;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace AgentBoard.Node.Agents;

/// <summary>
/// Sprint 4. MiniMax CLI adapter. Same stdin/stdout-JSON contract as
/// workbuddy; injects MiniMax_API_KEY from config (NOT parent env) so the
/// secret boundary is explicit. Resolves the CLI executable via
/// <see cref="CliLocator"/> so the worker can boot on a fresh box without
/// manual path configuration.
/// </summary>
public sealed class MiniMaxAdapter : IAgentAdapter
{
    private readonly IProcessExecutor _process;
    private readonly AgentsOptions _agents;
    private readonly AgentBoardOptions _agentboard;
    private readonly ILogger<MiniMaxAdapter> _log;

    public MiniMaxAdapter(
        IProcessExecutor process,
        IOptions<AgentsOptions> agents,
        IOptions<AgentBoardOptions> agentboard,
        ILogger<MiniMaxAdapter> log)
    {
        _process = process;
        _agents = agents.Value;
        _agentboard = agentboard.Value;
        _log = log;
    }

    public string AgentType => "minimax";

    public Task<AgentExecutionResult> ExecuteAsync(ExecutionContext context, CancellationToken ct)
    {
        var opts = _agents.MiniMax;
        var env = new Dictionary<string, string?>(StringComparer.OrdinalIgnoreCase);
        // PR-3: workload-aware prompt（不再是硬编码 "Handle proposal"）。
        var prompt = SharedAdapterHelpers.BuildWorkloadPrompt(
            agentName: "the MiniMax CLI",
            context: context).Replace("\r", "").Replace("\n", "\\n");

        // Locate the CLI on disk (probe known paths + where.exe). Throws
        // CliNotFoundException at startup so the operator sees a clear
        // error rather than a generic Win32Exception from Process.Start.
        var resolved = CliLocator.LocateMinimax(opts, _log);
        foreach (var (k, v) in resolved.ExtraEnv) env[k] = v;

        // P0-1（2026-09-01 review）：与 Codex/WorkBuddy 同样的身份注入。
        SharedAdapterHelpers.ApplyAgentBoardIdentity(
            env, opts.AgentBoardToken, _agentboard.StartupToken, _agentboard.ServerUrl);

        if (!string.IsNullOrWhiteSpace(opts.ApiKeyEnv))
        {
            // Adapter fetches the secret from its own config; ProcessExecutor
            // does not touch parent env (Sprint 5 isolation).
            var value = System.Environment.GetEnvironmentVariable(opts.ApiKeyEnv);
            if (!string.IsNullOrWhiteSpace(value))
            {
                env[opts.ApiKeyEnv] = value;
            }
            else
            {
                // Config requested a key, but it is not set in the worker's
                // environment. The CLI will fail with a generic auth error;
                // surface that early so the operator sees the real cause.
                throw new InvalidOperationException(
                    $"MiniMax adapter requires env var '{opts.ApiKeyEnv}' " +
                    "but it is not set in the worker process. " +
                    "Set it before starting the worker, or set ApiKeyEnv=\"\" " +
                    "in appsettings to opt out of key injection.");
            }
        }
        var spec = new ProcessSpec
        {
            Executable = resolved.Executable,
            Arguments = BuildArguments(opts.Arguments, prompt),
            WorkingDirectory = opts.WorkingDirectory,
            // minimax-cli's -p/--print consumes the next command-line
            // argument; it does not read the prompt from stdin.
            StdinPayload = null,
            Timeout = TimeSpan.FromMinutes(Math.Max(1, opts.TimeoutMinutes)),
            MaxOutputBytes = opts.MaxCapturedOutputChars,
            Environment = env,
            AgentType = AgentType,
        };
        return SharedAdapterHelpers.RunAndParseAsync(_process, spec, ct);
    }

    private static string[] BuildArguments(IReadOnlyList<string> configured, string prompt)
    {
        var arguments = configured.Count > 0
            ? configured.ToList()
            : new List<string> { "-p" };
        var promptFlag = arguments.FindIndex(
            value => value.Equals("-p", StringComparison.OrdinalIgnoreCase) ||
                     value.Equals("--print", StringComparison.OrdinalIgnoreCase));
        if (promptFlag < 0)
        {
            arguments.Add("-p");
            arguments.Add(prompt);
        }
        else
        {
            arguments.Insert(promptFlag + 1, prompt);
        }
        return arguments.ToArray();
    }
}
