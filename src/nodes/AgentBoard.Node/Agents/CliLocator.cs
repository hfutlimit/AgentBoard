using Microsoft.Extensions.Logging;

namespace AgentBoard.Node.Agents;

/// <summary>
/// A resolved CLI location: absolute path to the executable, the source
/// that produced it (for diagnostics), and any extra environment variables
/// the child process needs (PATH, USERPROFILE, CODEX_HOME, etc.). Node-based
/// entry scripts are represented as prefix arguments before configured args.
/// </summary>
public sealed record ResolvedCli(
    string Executable,
    string Source,
    IReadOnlyDictionary<string, string> ExtraEnv,
    IReadOnlyList<string> PrefixArguments);

/// <summary>
/// Thrown when a CLI cannot be located. The worker surfaces this at startup
/// (fail-fast) so operators see the real cause instead of a generic
/// Win32Exception from <c>Process.Start</c>.
/// </summary>
public sealed class CliNotFoundException(string agentType, string agentCmd, string searched)
    : Exception($"Could not locate {agentType} CLI: command='{agentCmd}'. Searched: {searched}")
{
    public string AgentType { get; } = agentType;
    public string AgentCmd { get; } = agentCmd;
}

/// <summary>
/// Resolves the absolute path of an agent CLI (Codex / MiniMax / WorkBuddy)
/// from the worker's <see cref="AgentOptions"/>. Probe order:
///
///   1. <c>opts.Command</c> is an absolute path that exists on disk.
///   2. Known Windows install locations for that CLI.
///   3. <c>where.exe</c> on the current PATH (Windows SearchPath semantics).
///
/// All probe methods are platform-neutral: on non-Windows hosts they
/// degrade gracefully (return null / throw CliNotFoundException) so
/// cross-platform builds do not require [SupportedOSPlatform("windows")]
/// on every caller.
/// </summary>
public static class CliLocator
{
    /// <summary>
    /// Locate the Codex CLI. Honors <c>opts.Command</c>; otherwise probes
    /// the npm-global bin directory and a few common per-user locations.
    /// </summary>
    public static ResolvedCli LocateCodex(AgentOptions opts, ILogger log)
    {
        var codexHome = Environment.GetEnvironmentVariable("CODEX_HOME");
        var candidates = new List<string>
        {
            // npm-global bin (e.g. %APPDATA%\npm\codex.cmd or node_modules\.bin\codex)
            Path.Combine(NpmGlobalBin(), "codex.cmd"),
            Path.Combine(NpmGlobalBin(), "codex.exe"),
            Path.Combine(NpmGlobalBin(), "codex"),
            // Per-user installs occasionally land here
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "openai", "app", "resources", "app.asar.unpacked", "cli", "bin", "codex.exe"),
            // CODEX_HOME parent (login state)
            codexHome is null ? "" : Path.Combine(codexHome, "codex.exe"),
        };
        return Resolve("codex", opts, candidates, BaseEnv(), log);
    }

    /// <summary>
    /// Locate the minimax CLI (npm package <c>minimax-cli</c>).
    /// On Windows, npm-global installs the wrapper as <c>minimax.cmd</c>;
    /// we surface the .cmd path so <see cref="ProcessExecutor"/> can wrap
    /// it via <c>cmd /c</c>.
    /// </summary>
    public static ResolvedCli LocateMinimax(AgentOptions opts, ILogger log)
    {
        var candidates = new List<string>
        {
            Path.Combine(NpmGlobalBin(), "minimax.cmd"),
            Path.Combine(NpmGlobalBin(), "minimax.exe"),
            Path.Combine(NpmGlobalBin(), "minimax"),
        };
        return Resolve("minimax", opts, candidates, BaseEnv(), log);
    }

    /// <summary>
    /// Locate the WorkBuddy codebuddy CLI. The CLI ships inside the
    /// WorkBuddy desktop app under <c>resources\app.asar.unpacked\cli\bin</c>;
    /// it is a Node shebang script and must be invoked via <c>node</c>.
    /// The .NET worker never invokes it directly — instead operators
    /// configure the command as <c>node "&lt;path&gt;\codebuddy" -p -y ...</c>
    /// or rely on this locator, which returns the .js entry point and the
    /// expected additional env vars.
    /// </summary>
    public static ResolvedCli LocateCodebuddy(AgentOptions opts, ILogger log)
    {
        var programFiles = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
        var programFilesX86 = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86);
        var localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);

        var candidates = new List<string>
        {
            // Standard WorkBuddy install locations (both 32 and 64-bit).
            Path.Combine(programFiles, "WorkBuddy", "resources", "app.asar.unpacked", "cli", "bin", "codebuddy"),
            Path.Combine(programFilesX86, "WorkBuddy", "resources", "app.asar.unpacked", "cli", "bin", "codebuddy"),
            // Per-user portable installs
            Path.Combine(localAppData, "Programs", "WorkBuddy", "resources", "app.asar.unpacked", "cli", "bin", "codebuddy"),
            Path.Combine(localAppData, "WorkBuddy", "resources", "app.asar.unpacked", "cli", "bin", "codebuddy"),
        };
        return WrapNodeScriptIfNeeded(
            Resolve("codebuddy", opts, candidates, BaseEnv(), log), log);
    }

    /// <summary>
    /// Generic resolution for an agent that has no well-known install
    /// locations on disk — e.g. the 千问办公 (qwen) agent, whose Command points
    /// directly at a Python invoker (<c>python.exe scripts/qwen_invoker.py</c>).
    /// Resolution relies solely on <c>opts.Command</c> (absolute path used as-is,
    /// bare name via where.exe). No Node-script wrapping.
    /// </summary>
    public static ResolvedCli LocateGeneric(string agentType, AgentOptions opts, ILogger log)
        => Resolve(agentType, opts, Array.Empty<string>(), BaseEnv(), log);

    /// <summary>
    /// Returns the user-level npm global bin directory (e.g.
    /// <c>%APPDATA%\npm</c>). Falls back to <c>%LOCALAPPDATA%\npm</c> if
    /// the user-level dir does not exist. Returns an empty string if
    /// neither is reachable.
    /// </summary>
    public static string NpmGlobalBin()
    {
        var roaming = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
        var appDataNpm = Path.Combine(roaming, "npm");
        if (Directory.Exists(appDataNpm)) return appDataNpm;

        var local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        var localNpm = Path.Combine(local, "npm");
        if (Directory.Exists(localNpm)) return localNpm;

        return appDataNpm; // best guess; Resolve() will check File.Exists
    }

    /// <summary>
    /// Run <c>where.exe</c> to resolve a bare command name against the
    /// current user's PATH. Returns the first match or null. The worker
    /// process' own PATH is not modified (Sprint 5 isolation), so we
    /// delegate to a fresh subshell that inherits the service-level
    /// env block.
    /// </summary>
    private static string? WhereOnPath(string command)
    {
        if (OperatingSystem.IsWindows() is false) return null;
        try
        {
            var psi = new System.Diagnostics.ProcessStartInfo
            {
                FileName = "where.exe",
                Arguments = command,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            using var p = System.Diagnostics.Process.Start(psi);
            if (p is null) return null;
            // Trim any extension list returned (where can return multiple
            // matches if the PATHEXT is ambiguous; we want the first).
            var first = p.StandardOutput.ReadLine();
            p.WaitForExit(2000);
            return string.IsNullOrWhiteSpace(first) ? null : first.Trim();
        }
        catch
        {
            return null;
        }
    }

    /// <summary>
    /// Common environment variables the spawned CLI needs. Kept as a
    /// minimal allow-list — adapters may layer more on top.
    /// </summary>
    private static IReadOnlyDictionary<string, string> BaseEnv()
    {
        var dict = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var name in new[] { "PATH", "USERPROFILE", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC",
                                     "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "APPDATA", "TEMP", "TMP", "PATHEXT" })
        {
            var v = Environment.GetEnvironmentVariable(name);
            if (!string.IsNullOrWhiteSpace(v)) dict[name] = v;
        }
        return dict;
    }

    private static ResolvedCli Resolve(
        string agentType,
        AgentOptions opts,
        IReadOnlyList<string> knownPaths,
        IReadOnlyDictionary<string, string> baseEnv,
        ILogger log)
    {
        // 1. Explicit config: absolute path that exists, or bare name on PATH.
        if (!string.IsNullOrWhiteSpace(opts.Command))
        {
            // Absolute path on disk — use as-is.
            if (Path.IsPathRooted(opts.Command) && File.Exists(opts.Command))
            {
                log.LogInformation("CLI {Agent}: using configured path {Path}", agentType, opts.Command);
                return new ResolvedCli(
                    opts.Command, $"config:{opts.Command}", baseEnv,
                    Array.Empty<string>());
            }

            // Bare name — try where.exe (covers PATHEXT, user PATH, etc.)
            var where = WhereOnPath(opts.Command);
            if (where is not null)
            {
                log.LogInformation("CLI {Agent}: resolved via where.exe {Path}", agentType, where);
                return new ResolvedCli(
                    where, $"where:{where}", baseEnv,
                    Array.Empty<string>());
            }

            // Last resort: keep the bare name; Process.Start will produce a
            // clear "file not found" error and the operator sees the exact
            // command that was attempted.
            log.LogWarning("CLI {Agent}: command '{Cmd}' not found on disk or PATH; " +
                           "will attempt to spawn as-is and let the OS report the error.",
                agentType, opts.Command);
            return new ResolvedCli(
                opts.Command, $"env-as-is:{opts.Command}", baseEnv,
                Array.Empty<string>());
        }

        // 2. Known install locations.
        foreach (var candidate in knownPaths)
        {
            if (!string.IsNullOrWhiteSpace(candidate) && File.Exists(candidate))
            {
                log.LogInformation("CLI {Agent}: found at known location {Path}", agentType, candidate);
                return new ResolvedCli(
                    candidate, $"known-path:{candidate}", baseEnv,
                    Array.Empty<string>());
            }
        }

        // 3. Last-ditch: where on the bare name.
        var whereDefault = WhereOnPath(agentType);
        if (whereDefault is not null)
        {
            log.LogInformation("CLI {Agent}: resolved via default where.exe probe {Path}",
                agentType, whereDefault);
            return new ResolvedCli(
                whereDefault, $"where-default:{whereDefault}", baseEnv,
                Array.Empty<string>());
        }

        var searched = string.Join(" | ", knownPaths.Where(p => !string.IsNullOrWhiteSpace(p)));
        throw new CliNotFoundException(agentType, opts.Command ?? "", searched);
    }

    private static ResolvedCli WrapNodeScriptIfNeeded(ResolvedCli resolved, ILogger log)
    {
        var executable = resolved.Executable;
        var extension = Path.GetExtension(executable);
        var isNodeScript = string.IsNullOrEmpty(extension) ||
            extension.Equals(".js", StringComparison.OrdinalIgnoreCase) ||
            extension.Equals(".mjs", StringComparison.OrdinalIgnoreCase) ||
            extension.Equals(".cjs", StringComparison.OrdinalIgnoreCase);
        if (!File.Exists(executable) || !isNodeScript)
        {
            return resolved;
        }

        // WorkBuddy bundles `codebuddy` as an extensionless Node shebang
        // script. Process.Start cannot execute that file directly on Windows,
        // so resolve node.exe and prepend the script as argv[0].
        var node = WhereOnPath("node.exe") ?? WhereOnPath("node");
        if (node is null)
        {
            throw new CliNotFoundException(
                "node", "node.exe",
                $"required to launch WorkBuddy script {executable}; current PATH");
        }

        log.LogInformation(
            "CLI codebuddy: launching Node script {Script} via {Node}",
            executable, node);
        return new ResolvedCli(
            node,
            $"{resolved.Source};via-node:{node}",
            resolved.ExtraEnv,
            new[] { executable });
    }
}
