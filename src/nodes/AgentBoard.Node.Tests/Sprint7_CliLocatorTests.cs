using System.Runtime.InteropServices;
using AgentBoard.Node.Agents;
using AgentBoard.Node.Process;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Xunit;

namespace AgentBoard.Node.Tests;

/// <summary>
/// Sprint 7: CliLocator probe semantics. Pure (no subprocesses, no real
/// filesystem writes) — uses <see cref="CliLocator.NpmGlobalBin"/> for
/// discovery and asserts ordering, source tag, and the fail-fast
/// <see cref="CliNotFoundException"/> contract.
/// </summary>
public sealed class Sprint7_CliLocatorTests
{
    [Fact]
    public void LocateCodex_uses_configured_absolute_path_when_present()
    {
        if (!RuntimeInformation.IsOSPlatform(OSPlatform.Windows)) return; // appveyor skip

        // Use a path that always exists on Windows: %WINDIR%\system32\cmd.exe.
        var opts = new AgentOptions { Command = Path.Combine(Environment.SystemDirectory, "cmd.exe") };
        var resolved = CliLocator.LocateCodex(opts, NullLogger.Instance);

        Assert.Equal(opts.Command, resolved.Executable);
        Assert.StartsWith("config:", resolved.Source);
    }

    [Fact]
    public void LocateCodex_with_bare_name_does_not_throw_when_resolver_finds_nothing()
    {
        if (!RuntimeInformation.IsOSPlatform(OSPlatform.Windows)) return;

        // A bare name that does not exist anywhere on PATH; the locator
        // should still return a ResolvedCli (with the bare name + warning
        // source) so the adapter can produce a clear "file not found"
        // error rather than a startup-time exception.
        var opts = new AgentOptions { Command = "this-cli-does-not-exist-anywhere-12345" };
        var resolved = CliLocator.LocateCodex(opts, NullLogger.Instance);

        Assert.Equal("this-cli-does-not-exist-anywhere-12345", resolved.Executable);
        Assert.StartsWith("env-as-is:", resolved.Source);
    }

    [Fact]
    public void NpmGlobalBin_prefers_appdata_npm()
    {
        if (!RuntimeInformation.IsOSPlatform(OSPlatform.Windows)) return;

        // %APPDATA%\npm always exists on Windows (it's the user shell folder).
        var npmBin = CliLocator.NpmGlobalBin();
        Assert.False(string.IsNullOrWhiteSpace(npmBin));
        Assert.EndsWith("npm", npmBin, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void BaseEnv_contains_path_for_windows_cli_resolution()
    {
        // Indirect: the dictionary the locator passes back must include
        // PATH so the spawned CLI can find its own dependencies (e.g. node).
        var opts = new AgentOptions { Command = "never-resolved" };
        var resolved = CliLocator.LocateCodex(opts, NullLogger.Instance);

        Assert.True(resolved.ExtraEnv.ContainsKey("PATH"),
            "Locator must surface PATH so codex/minimax/codebuddy can resolve node etc.");
        if (OperatingSystem.IsWindows()
            && !string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("SYSTEMDRIVE")))
        {
            Assert.True(resolved.ExtraEnv.ContainsKey("SYSTEMDRIVE"),
                "Windows CLIs must not expand %SystemDrive% as a literal relative directory");
        }
    }

    [Fact]
    public void CliNotFoundException_carries_agent_type_and_command()
    {
        var ex = new CliNotFoundException("minimax", "x", "searched-list");

        Assert.Equal("minimax", ex.AgentType);
        Assert.Equal("x", ex.AgentCmd);
        Assert.Contains("searched-list", ex.Message);
    }

    [Fact]
    public void Configured_bare_name_is_preserved_for_os_level_diagnostics()
    {
        // An explicitly configured bare command may be supplied later through
        // the service environment. Preserve it so ProcessExecutor can return
        // the operating system's concrete spawn error.
        var opts = new AgentOptions { Command = "absent-cli-xyz" };
        var resolved = CliLocator.LocateCodex(opts, NullLogger.Instance);
        Assert.Equal("absent-cli-xyz", resolved.Executable);
    }

    [Fact]
    public void LocateCodebuddy_wraps_extensionless_node_script()
    {
        if (!RuntimeInformation.IsOSPlatform(OSPlatform.Windows)) return;
        var node = FindOnPath("node.exe");
        if (node is null) return;

        var script = Path.Combine(Path.GetTempPath(), $"codebuddy-{Guid.NewGuid():N}");
        File.WriteAllText(script, "console.log('ok');");
        try
        {
            var resolved = CliLocator.LocateCodebuddy(
                new AgentOptions { Command = script }, NullLogger.Instance);

            Assert.Equal(node, resolved.Executable, ignoreCase: true);
            Assert.Equal(new[] { script }, resolved.PrefixArguments);
            Assert.Contains("via-node", resolved.Source);
        }
        finally
        {
            File.Delete(script);
        }
    }

    [Fact]
    public async Task MiniMax_adapter_forwards_configured_arguments()
    {
        var executor = new RecordingExecutor();
        var options = new AgentsOptions
        {
            MiniMax = new AgentOptions
            {
                Command = Environment.ProcessPath!,
                Arguments = new[] { "-p", "--output-format", "text" },
                ApiKeyEnv = "",
            },
        };
        var adapter = new MiniMaxAdapter(
            executor, Options.Create(options), Options.Create(new AgentBoardOptions()),
            NullLogger<MiniMaxAdapter>.Instance);

        await adapter.ExecuteAsync(
            new AgentBoard.Node.ExecutionContext(
                1, "proposal:42:0:minimax", "proposal", 42, 0,
                "minimax", "{}", null),
            CancellationToken.None);

        Assert.Equal("-p", executor.Spec!.Arguments[0]);
        Assert.Contains("Handle proposal 42", executor.Spec.Arguments[1]);
        Assert.Equal("--output-format", executor.Spec.Arguments[2]);
        Assert.Equal("text", executor.Spec.Arguments[3]);
        Assert.Null(executor.Spec.StdinPayload);
    }

    private static string? FindOnPath(string executable)
    {
        foreach (var directory in (Environment.GetEnvironmentVariable("PATH") ?? "")
                     .Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries))
        {
            var candidate = Path.Combine(directory.Trim('"'), executable);
            if (File.Exists(candidate)) return candidate;
        }
        return null;
    }

    private sealed class RecordingExecutor : IProcessExecutor
    {
        public ProcessSpec? Spec { get; private set; }

        public Task<ProcessResult> ExecuteAsync(ProcessSpec spec, CancellationToken ct)
        {
            Spec = spec;
            return Task.FromResult(new ProcessResult
            {
                ExitCode = 0,
                RedactedOutput = "{\"action\":\"finalize\"}",
            });
        }
    }
}
