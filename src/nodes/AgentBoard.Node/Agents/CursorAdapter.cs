using AgentBoard.Node.Process;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace AgentBoard.Node.Agents;

/// <summary>
/// Cursor CLI adapter. Headless print mode: <c>agent -p --force --trust
/// --approve-mcps --output-format json</c>. The prompt is supplied on stdin
/// so long Worker-owned workloads are not truncated by the Windows command
/// line. Resolves the <c>agent</c> executable via <see cref="CliLocator"/>.
/// </summary>
public sealed class CursorAdapter : IAgentAdapter
{
    private readonly IProcessExecutor _process;
    private readonly AgentsOptions _agents;
    private readonly AgentBoardOptions _agentboard;
    private readonly ILogger<CursorAdapter> _log;

    public CursorAdapter(
        IProcessExecutor process,
        IOptions<AgentsOptions> agents,
        IOptions<AgentBoardOptions> agentboard,
        ILogger<CursorAdapter> log)
    {
        _process = process;
        _agents = agents.Value;
        _agentboard = agentboard.Value;
        _log = log;
    }

    public string AgentType => "cursor";

    public async Task<AgentExecutionResult> ExecuteAsync(ExecutionContext context, CancellationToken ct)
    {
        var opts = _agents.Cursor;
        var prompt = SharedAdapterHelpers.BuildWorkloadPrompt(
            agentName: "the Cursor CLI (agent)",
            context: context);
        var arguments = (opts.Arguments is { Length: > 0 }
            ? opts.Arguments
            : new[] { "-p", "--force", "--trust", "--approve-mcps", "--output-format", "json" }).ToList();
        if (!string.IsNullOrWhiteSpace(opts.Model)
            && !arguments.Any(argument => argument is "--model" or "-m"
                || argument.StartsWith("--model=", StringComparison.Ordinal)))
        {
            arguments.Add("--model");
            arguments.Add(opts.Model);
        }
        var resolved = CliLocator.LocateCursor(opts, _log);
        var env = BuildEnvironment(opts, resolved.ExtraEnv);
        SharedAdapterHelpers.ApplyAgentBoardIdentity(
            env, opts.AgentBoardToken, _agentboard.StartupToken, _agentboard.ServerUrl);
        var spec = new ProcessSpec
        {
            Executable = resolved.Executable,
            WorkingDirectory = string.IsNullOrWhiteSpace(context.WorkingDirectory)
                ? opts.WorkingDirectory : context.WorkingDirectory,
            Arguments = resolved.PrefixArguments.Concat(arguments).ToArray(),
            StdinPayload = prompt,
            Environment = env,
            Timeout = TimeSpan.FromMinutes(Math.Max(1, opts.TimeoutMinutes)),
            MaxOutputBytes = opts.MaxCapturedOutputChars,
            AgentType = AgentType,
        };
        _log.LogInformation(
            "CursorAdapter.Execute: workload={Workload} id={Id} cmd='{Cmd}' model={Model}",
            context.WorkloadType, context.WorkloadId, spec.Executable, opts.Model);
        var result = await _process.ExecuteAsync(spec, ct);
        var output = result.RedactedOutput ?? "";
        return new AgentExecutionResult(
            Success: result.ExitCode == 0 && !result.TimedOut && !result.Cancelled,
            OutputJson: SharedAdapterHelpers.TryExtractCursorJson(output),
            ErrorMessage: result.Cancelled ? "cancelled"
                : result.TimedOut ? "timeout"
                : result.ExitCode == 0 ? null : $"exit {result.ExitCode}: {result.StderrTail}",
            ExitCode: result.ExitCode,
            Duration: result.Duration,
            TimedOut: result.TimedOut,
            Cancelled: result.Cancelled);
    }

    private static Dictionary<string, string?> BuildEnvironment(
        AgentOptions opts,
        IReadOnlyDictionary<string, string> locatorEnv)
    {
        var env = new Dictionary<string, string?>(StringComparer.OrdinalIgnoreCase);
        foreach (var (k, v) in locatorEnv) env[k] = v;
        // Cursor 1.0+ uses CURSOR_TOKEN (Bearer-style API token); the older
        // CURSOR_API_KEY is still accepted by some installations. Forward both
        // so whichever the user set on the host reaches the CLI. CURSOR_CONFIG_DIR
        // and HOME round out the auth + config bootstrap the CLI expects.
        foreach (var name in new[] { "CURSOR_TOKEN", "CURSOR_API_KEY", "CURSOR_CONFIG_DIR", "HOME" })
        {
            var value = System.Environment.GetEnvironmentVariable(name);
            if (!string.IsNullOrWhiteSpace(value) && !env.ContainsKey(name)) env[name] = value;
        }
        if (!string.IsNullOrWhiteSpace(opts.ApiKeyEnv))
        {
            var value = System.Environment.GetEnvironmentVariable(opts.ApiKeyEnv);
            if (!string.IsNullOrWhiteSpace(value)) env[opts.ApiKeyEnv] = value;
        }
        return env;
    }
}
