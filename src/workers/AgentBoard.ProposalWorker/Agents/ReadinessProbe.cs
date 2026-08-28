// SPDX-License-Identifier: MIT
using AgentBoard.ProposalWorker.Agents;
using AgentBoard.ProposalWorker.Process;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace AgentBoard.ProposalWorker.Agents;

/// <summary>
/// Startup CLI readiness probe. For each registered agent, resolve the CLI
/// via <see cref="CliLocator"/> and run a short <c>--version</c> probe under
/// the worker's own identity. The result is stored on <see cref="WorkerState"/>
/// so /health distinguishes <c>registered=true</c> from <c>ready=true</c>.
///
/// Fix for #5 in the 2026-08-28 review: prior versions only checked DI
/// presence, which let a "green" installer report success even when the
/// CLI was missing, the API key was unset, or the CLI failed to spawn.
///
/// FakeAdapter never spawns anything, so it is always reported ready.
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
                var (ready, error) = await ProbeOneAsync(agentType, ct);
                _state.SetAgentReady(agentType, ready, error);
                if (ready)
                {
                    _log.LogInformation("Readiness: {Agent} = ready", agentType);
                }
                else
                {
                    _log.LogWarning("Readiness: {Agent} = NOT ready ({Error})", agentType, error);
                }
            }
            catch (Exception ex)
            {
                // Defensive: any unexpected exception must not crash startup.
                _state.SetAgentReady(agentType, false, ex.Message);
                _log.LogError(ex, "Readiness: {Agent} probe threw", agentType);
            }
        }
    }

    private async Task<(bool Ready, string? Error)> ProbeOneAsync(string agentType, CancellationToken ct)
    {
        // FakeAdapter is in-process; it never spawns a CLI.
        if (string.Equals(agentType, "fake", StringComparison.OrdinalIgnoreCase))
        {
            return (true, null);
        }

        var opts = ResolveOpts(agentType);
        if (opts is null)
        {
            return (false, $"no AgentOptions bound for agent_type '{agentType}'");
        }

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
            return (false, ex.Message);
        }

        // Build a small probe spec: spawn the resolved CLI with `--version`
        // and a short timeout. No stdin payload, no env (the probe verifies
        // the binary itself, not business state).
        var probeArgs = new List<string>(resolved.PrefixArguments);
        // The `--version` flag is the de-facto standard; if the CLI rejects
        // it the probe surfaces that immediately. We do NOT pre-pend the
        // adapter's `opts.Arguments` because some adapters (e.g. codex)
        // already include `exec` which makes `--version` ambiguous.
        probeArgs.Add("--version");

        var env = new Dictionary<string, string?>(StringComparer.OrdinalIgnoreCase);
        foreach (var (k, v) in resolved.ExtraEnv) env[k] = v;

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
                return (false, $"timeout after {ProbeTimeout.TotalSeconds:F0}s");
            }
            if (result.Cancelled)
            {
                return (false, "cancelled");
            }
            if (result.ExitCode != 0)
            {
                var tail = string.IsNullOrEmpty(result.StderrTail) ? result.OutputTail : result.StderrTail;
                return (false, $"exit {result.ExitCode}: {Truncate(tail, 200)}");
            }
            return (true, null);
        }
        catch (Exception ex)
        {
            return (false, ex.Message);
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
