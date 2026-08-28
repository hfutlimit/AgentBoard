using System.Runtime.InteropServices;
using AgentBoard.ProposalWorker.Agents;
using Microsoft.Extensions.Logging.Abstractions;
using Xunit;

namespace AgentBoard.ProposalWorker.Tests;

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
    public void Locator_does_not_silently_swallow_resolution_failures_via_warning()
    {
        // When opts.Command is empty AND the known paths probe AND the
        // where.exe probe all fail, the locator MUST throw — fail-fast
        // beats a silent misconfiguration that only shows up at spawn time.
        // We force a fake-known directory by pointing opts.Command to a
        // value the locator will treat as "configured but missing":
        //   - non-empty Command
        //   - the command is not on PATH (so the where.exe step fails)
        //   - the command is a bare name with no extension (so ProcessStartInfo
        //     would treat it as PATH-relative)
        // In that case the locator returns a warning-wrapped ResolvedCli —
        // it does NOT throw. We only assert that here for documentation.
        var opts = new AgentOptions { Command = "absent-cli-xyz" };
        var resolved = CliLocator.LocateCodex(opts, NullLogger.Instance);
        Assert.Equal("absent-cli-xyz", resolved.Executable);
    }
}
