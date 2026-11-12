using System.Text.Json;
using AgentBoard.Node.Process;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace AgentBoard.Node.Agents;

/// <summary>
/// Qoder CN CLI adapter. Headless print mode: <c>-p --output-format json
/// --permission-mode bypass_permissions --no-session-persistence</c>, with the
/// prompt on stdin so long Worker-owned workloads are not truncated by the
/// Windows command line.
///
/// Unlike Codex / Cursor this adapter loads no MCP servers and injects no
/// AgentBoard identity: the Worker-owned execution protocol forbids the
/// provider from calling MCP or mutating state, and only the fenced Worker
/// persists the returned evidence.
/// </summary>
public sealed class QoderAdapter : IAgentAdapter
{
    internal static readonly string[] HeadlessArguments =
    [
        "-p", "--output-format", "json",
        "--permission-mode", "bypass_permissions",
        "--no-session-persistence",
        "--strict-mcp-config", "--mcp-config", "{\"mcpServers\":{}}",
    ];

    private readonly IProcessExecutor _process;
    private readonly AgentsOptions _agents;
    private readonly ILogger<QoderAdapter> _log;

    public QoderAdapter(
        IProcessExecutor process,
        IOptions<AgentsOptions> agents,
        ILogger<QoderAdapter> log)
    {
        _process = process;
        _agents = agents.Value;
        _log = log;
    }

    public string AgentType => "qoder";

    public async Task<AgentExecutionResult> ExecuteAsync(ExecutionContext context, CancellationToken ct)
    {
        var opts = _agents.Qoder;
        var prompt = SharedAdapterHelpers.BuildWorkloadPrompt(
            agentName: "the Qoder CN CLI (qoderclicn)",
            context: context);
        var arguments = (opts.Arguments is { Length: > 0 }
            ? opts.Arguments
            : HeadlessArguments).ToList();
        if (!string.IsNullOrWhiteSpace(opts.Model)
            && !arguments.Any(argument => argument is "--model" or "-m"
                || argument.StartsWith("--model=", StringComparison.Ordinal)))
        {
            arguments.Add("--model");
            arguments.Add(opts.Model);
        }
        var resolved = CliLocator.LocateQoder(opts, _log);
        var spec = new ProcessSpec
        {
            Executable = resolved.Executable,
            WorkingDirectory = string.IsNullOrWhiteSpace(context.WorkingDirectory)
                ? opts.WorkingDirectory : context.WorkingDirectory,
            Arguments = resolved.PrefixArguments.Concat(arguments).ToArray(),
            StdinPayload = prompt,
            Environment = BuildEnvironment(opts, resolved.ExtraEnv),
            Timeout = TimeSpan.FromMinutes(Math.Max(1, opts.TimeoutMinutes)),
            MaxOutputBytes = opts.MaxCapturedOutputChars,
            AgentType = AgentType,
        };
        _log.LogInformation(
            "QoderAdapter.Execute: workload={Workload} id={Id} cmd='{Cmd}' model={Model}",
            context.WorkloadType, context.WorkloadId, spec.Executable, opts.Model);
        var result = await _process.ExecuteAsync(spec, ct);
        var output = result.RedactedOutput ?? "";
        // Qoder can exit 0 while the envelope reports a failed turn, so the
        // exit code alone is not a success signal.
        var envelopeError = ReadEnvelopeError(output);
        return new AgentExecutionResult(
            Success: result.ExitCode == 0 && !result.TimedOut && !result.Cancelled && envelopeError is null,
            OutputJson: SharedAdapterHelpers.TryExtractResultEnvelopeJson(output),
            ErrorMessage: result.Cancelled ? "cancelled"
                : result.TimedOut ? "timeout"
                : result.ExitCode != 0 ? $"exit {result.ExitCode}: {result.StderrTail}"
                : envelopeError,
            ExitCode: result.ExitCode,
            Duration: result.Duration,
            TimedOut: result.TimedOut,
            Cancelled: result.Cancelled);
    }

    private static string? ReadEnvelopeError(string output)
    {
        try
        {
            using var document = JsonDocument.Parse(output.Trim());
            var root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object
                || !root.TryGetProperty("is_error", out var flag)
                || flag.ValueKind != JsonValueKind.True)
                return null;
            var subtype = root.TryGetProperty("subtype", out var value) && value.ValueKind == JsonValueKind.String
                ? value.GetString() : "unknown";
            return $"provider reported is_error ({subtype})";
        }
        catch (JsonException)
        {
            return null;
        }
    }

    private static Dictionary<string, string?> BuildEnvironment(
        AgentOptions opts,
        IReadOnlyDictionary<string, string> locatorEnv)
    {
        // ProcessExecutor starts from an empty environment, so only the
        // locator allow-list plus the vars below reach the CLI. Desktop SDK
        // markers (QODER_AGENT_SDK_*, QODERCLI_RUNTIME_PACKAGING,
        // QODERCN_SESSION_TYPE) must never be added: they make the CLI exit 42
        // with sdk_worker_removed instead of running the workload.
        var env = new Dictionary<string, string?>(StringComparer.OrdinalIgnoreCase);
        foreach (var (k, v) in locatorEnv) env[k] = v;
        // QODERCN_PERSONAL_ACCESS_TOKEN is forwarded unconditionally so an
        // unattended profile authenticates without any per-agent ApiKeyEnv
        // setup; a prior `qoderclicn login` works too when it is absent.
        foreach (var name in new[] { "QODERCN_CONFIG_DIR", "QODERCN_PERSONAL_ACCESS_TOKEN" })
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