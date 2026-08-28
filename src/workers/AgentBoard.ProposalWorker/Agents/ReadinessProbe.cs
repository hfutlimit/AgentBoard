// SPDX-License-Identifier: MIT
using AgentBoard.ProposalWorker.Agents;
using AgentBoard.ProposalWorker.Process;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace AgentBoard.ProposalWorker.Agents;

/// <summary>
/// Startup CLI readiness probe. For each registered agent the probe splits
/// the check into two distinct gates so a false "ready" is no longer
/// possible:
///
///   * <c>cli_ready</c>     — the CLI binary resolves and exits 0 on --version.
///   * <c>credential_ready</c> — every required credential (e.g.
///                              <c>ApiKeyEnv</c>) is present in the worker
///                              process environment.
///
/// The combined <see cref="AgentReadiness.Ready"/> bool is what the
/// installer / /health report honours. Fix for #5 (initial) and #6
/// (ApiKeyEnv false positive) in the 2026-08-28 review.
///
/// FakeAdapter never spawns anything, so it is always <c>Ready=true</c>.
/// </summary>
public sealed class ReadinessProbe
{
    private readonly IAgentAdapterRegistry _registry;
    private readonly IProcessExecutor _process;
    private readonly AgentsOptions _agents;
    private readonly WorkerState _state;
    private readonly ILogger<ReadinessProbe> _log;
    private static readonly TimeSpan ProbeTimeout = TimeSpan.FromSeconds(10);

    public ReadinessProbe(
        IAgentAdapterRegistry registry,
        IProcessExecutor process,
        IOptions<AgentsOptions> agents,
        WorkerState state,
        ILogger<ReadinessProbe> log)
    {
        _registry = registry;
        _process = process;
        _agents = agents.Value;
        _state = state;
        _log = log;
    }

    public async Task RunAllAsync(CancellationToken ct)
    {
        foreach (var agentType in _registry.RegisteredAgents)
        {
            try
            {
                var report = await ProbeOneAsync(agentType, ct);
                _state.SetAgentReport(agentType, report);
                if (report.Ready)
                {
                    _log.LogInformation(
                        "Readiness: {Agent} = ready (cli={Cli}, credential={Cred})",
                        agentType, report.CliReady, report.CredentialReady);
                }
                else
                {
                    _log.LogWarning(
                        "Readiness: {Agent} = NOT ready (cli={Cli} {CliError}; credential={Cred} {CredError})",
                        agentType, report.CliReady, report.CliError,
                        report.CredentialReady, report.CredentialError);
                }
            }
            catch (Exception ex)
            {
                _state.SetAgentReport(agentType,
                    new AgentReadiness(CliReady: false, CliError: ex.Message,
                                       CredentialReady: false, CredentialError: "probe threw"));
                _log.LogError(ex, "Readiness: {Agent} probe threw", agentType);
            }
        }
    }

    private async Task<AgentReadiness> ProbeOneAsync(string agentType, CancellationToken ct)
    {
        // FakeAdapter is in-process; it never spawns a CLI.
        if (string.Equals(agentType, "fake", StringComparison.OrdinalIgnoreCase))
        {
            return AgentReadiness.AllOk();
        }

        var opts = ResolveOpts(agentType);
        if (opts is null)
        {
            return new AgentReadiness(
                CliReady: false,
                CliError: $"no AgentOptions bound for agent_type '{agentType}'",
                CredentialReady: true,
                CredentialError: null);
        }

        // Credential gate — independent of the CLI invocation so a
        // missing env var surfaces even when the CLI itself is present.
        var cred = CheckCredential(opts);
        if (!cred.CredentialReady)
        {
            // No point spawning the binary if the credential is missing;
            // the adapter will throw at execution time. Surface it now.
            // We still try the CLI binary so the operator gets both
            // failure modes in the report.
            var cliOnly = await ProbeCliAsync(agentType, opts, ct);
            return cliOnly.WithCredential(cred.CredentialReady, cred.CredentialError);
        }

        var cli = await ProbeCliAsync(agentType, opts, ct);
        return cli.WithCredential(cred.CredentialReady, cred.CredentialError);
    }

    private static AgentReadiness CheckCredential(AgentOptions opts)
    {
        // Only API-key mode is currently a hard requirement. CODEX_HOME
        // (ChatGPT login) and other env-driven auth flows are not
        // detectable from the worker process; the operator must verify
        // those manually. We surface what we can.
        if (string.IsNullOrWhiteSpace(opts.ApiKeyEnv))
        {
            // The adapter does not require an API key from env (e.g. it
            // uses CODEX_HOME login, or the CLI is not used). Treat as
            // ready.
            return new AgentReadiness(CliReady: true, CliError: null,
                                      CredentialReady: true, CredentialError: null);
        }
        var v = Environment.GetEnvironmentVariable(opts.ApiKeyEnv);
        if (!string.IsNullOrWhiteSpace(v))
        {
            return new AgentReadiness(CliReady: true, CliError: null,
                                      CredentialReady: true, CredentialError: null);
        }
        return new AgentReadiness(
            CliReady: true, CliError: null,
            CredentialReady: false,
            CredentialError: $"env var '{opts.ApiKeyEnv}' is not set; the adapter will throw InvalidOperationException on first use");
    }

    private async Task<AgentReadiness> ProbeCliAsync(string agentType, AgentOptions opts, CancellationToken ct)
    {
        ResolvedCli resolved;
        try
        {
            resolved = agentType.ToLowerInvariant() switch
            {
                "workbuddy" => CliLocator.LocateCodebuddy(opts, _log),
                "minimax" => CliLocator.LocateMinimax(opts, _log),
                "codex" => CliLocator.LocateCodex(opts, _log),
                _ => throw new CliNotFoundException(agentType, opts.Command ?? "", "no locator wired"),
            };
        }
        catch (CliNotFoundException ex)
        {
            return new AgentReadiness(CliReady: false, CliError: ex.Message,
                                       CredentialReady: true, CredentialError: null);
        }

        // Build a small probe spec: spawn the resolved CLI with `--version`
        // and a short timeout. No stdin payload (the probe verifies the
        // binary itself, not business state).
        var probeArgs = new List<string>(resolved.PrefixArguments);
        probeArgs.Add("--version");

        var env = new Dictionary<string, string?>(StringComparer.OrdinalIgnoreCase);
        foreach (var (k, v) in resolved.ExtraEnv) env[k] = v;
        // Also include the API key in the probe env if set, so the CLI does
        // not fail to start because of a missing variable that the real
        // invocation would have.
        if (!string.IsNullOrWhiteSpace(opts.ApiKeyEnv))
        {
            var apiKey = Environment.GetEnvironmentVariable(opts.ApiKeyEnv);
            if (!string.IsNullOrWhiteSpace(apiKey)) env[opts.ApiKeyEnv] = apiKey;
        }

        var spec = new ProcessSpec
        {
            Executable = resolved.Executable,
            Arguments = probeArgs.ToArray(),
            WorkingDirectory = opts.WorkingDirectory,
            StdinPayload = null,
            Timeout = ProbeTimeout,
            MaxOutputBytes = 4 * 1024,
            Environment = env,
            AgentType = agentType,
        };

        try
        {
            var result = await _process.ExecuteAsync(spec, ct);
            if (result.TimedOut)
            {
                return new AgentReadiness(CliReady: false,
                    CliError: $"timeout after {ProbeTimeout.TotalSeconds:F0}s",
                    CredentialReady: true, CredentialError: null);
            }
            if (result.Cancelled)
            {
                return new AgentReadiness(CliReady: false, CliError: "cancelled",
                                          CredentialReady: true, CredentialError: null);
            }
            if (result.ExitCode != 0)
            {
                var tail = string.IsNullOrEmpty(result.StderrTail) ? result.OutputTail : result.StderrTail;
                return new AgentReadiness(CliReady: false,
                    CliError: $"exit {result.ExitCode}: {Truncate(tail, 200)}",
                    CredentialReady: true, CredentialError: null);
            }
            return new AgentReadiness(CliReady: true, CliError: null,
                                       CredentialReady: true, CredentialError: null);
        }
        catch (Exception ex)
        {
            return new AgentReadiness(CliReady: false, CliError: ex.Message,
                                       CredentialReady: true, CredentialError: null);
        }
    }

    private AgentOptions? ResolveOpts(string agentType) => agentType.ToLowerInvariant() switch
    {
        "workbuddy" => _agents.WorkBuddy,
        "minimax" => _agents.MiniMax,
        "codex" => _agents.Codex,
        "fake" => _agents.Fake,
        _ => null,
    };

    private static string Truncate(string s, int max) =>
        s.Length <= max ? s : s[..max] + "...";
}

/// <summary>
/// Per-agent readiness split into <c>cli_ready</c> and
/// <c>credential_ready</c>. The combined <see cref="Ready"/> bool is what
/// the installer / /health report honours.
/// </summary>
public sealed record AgentReadiness(bool CliReady, string? CliError, bool CredentialReady, string? CredentialError)
{
    /// <summary>True iff both the CLI binary and the required credentials are present.</summary>
    public bool Ready => CliReady && CredentialReady;

    /// <summary>All gates pass. Used for fake / non-spawning adapters.</summary>
    public static AgentReadiness AllOk() =>
        new(true, null, true, null);

    /// <summary>Replace the credential half of an existing readiness report.</summary>
    public AgentReadiness WithCredential(bool ready, string? error) =>
        new(CliReady, CliError, ready, error);
}
