using System.Runtime.InteropServices;
using System.Text;
using AgentBoard.Node.Platform;
using Xunit;

namespace AgentBoard.Node.Tests;

/// <summary>
/// M0.3: <see cref="IProcessRunner"/> behaviour on whichever host the suite
/// runs on. Every test spawns a real short-lived process, so command selection
/// is the only platform-specific part; assertions are shared.
/// </summary>
public sealed class M0_ProcessRunnerTests
{
    private static readonly bool IsWindows =
        RuntimeInformation.IsOSPlatform(OSPlatform.Windows);

    private static string Shell => IsWindows
        ? Environment.GetEnvironmentVariable("ComSpec") ?? "cmd.exe"
        : "/bin/sh";

    // -------------------------------------------------------------------------
    // Lifecycle
    // -------------------------------------------------------------------------

    [Fact]
    public async Task Start_runs_a_command_and_waits_for_a_zero_exit_code()
    {
        var runner = PlatformFactory.CreateProcessRunner();

        await using var handle = await runner.StartAsync(
            Shell, EchoArgs("hello"), MinimalEnv(), CancellationToken.None);

        var exit = await runner.WaitAsync(handle, 30_000, CancellationToken.None);

        Assert.False(exit.TimedOut);
        Assert.Equal(0, exit.ExitCode);
        Assert.True(handle.HasExited);
    }

    [Fact]
    public async Task ReadOutput_returns_the_child_stdout()
    {
        var runner = PlatformFactory.CreateProcessRunner();

        await using var handle = await runner.StartAsync(
            Shell, EchoArgs("agentboard"), MinimalEnv(), CancellationToken.None);
        await runner.WaitAsync(handle, 30_000, CancellationToken.None);

        var output = await runner.ReadOutputAsync(handle, 4096, CancellationToken.None);

        Assert.Contains("agentboard", Encoding.UTF8.GetString(output));
    }

    [Fact]
    public async Task ReadOutput_honours_the_byte_limit()
    {
        var runner = PlatformFactory.CreateProcessRunner();

        await using var handle = await runner.StartAsync(
            Shell, EchoArgs(new string('x', 200)), MinimalEnv(), CancellationToken.None);
        await runner.WaitAsync(handle, 30_000, CancellationToken.None);

        var output = await runner.ReadOutputAsync(handle, 8, CancellationToken.None);

        Assert.Equal(8, output.Length);
    }

    [Fact]
    public async Task Wait_reports_timed_out_instead_of_blocking_forever()
    {
        var runner = PlatformFactory.CreateProcessRunner();

        await using var handle = await runner.StartAsync(
            Shell, SleepArgs(60), MinimalEnv(), CancellationToken.None);

        var exit = await runner.WaitAsync(handle, 300, CancellationToken.None);

        Assert.True(exit.TimedOut);
        Assert.Equal(-1, exit.ExitCode);
    }

    // -------------------------------------------------------------------------
    // Teardown
    // -------------------------------------------------------------------------

    [Fact]
    public async Task Kill_stops_a_long_running_child()
    {
        var runner = PlatformFactory.CreateProcessRunner();

        await using var handle = await runner.StartAsync(
            Shell, SleepArgs(60), MinimalEnv(), CancellationToken.None);

        await runner.KillAsync(handle, ProcessSignal.Kill, CancellationToken.None);
        var exit = await runner.WaitAsync(handle, 15_000, CancellationToken.None);

        Assert.False(exit.TimedOut);
    }

    [Fact]
    public async Task Terminate_stops_a_child_that_is_waiting_on_stdin()
    {
        if (!IsWindows) return; // uses cmd's `set /p` to block on the input pipe

        var runner = PlatformFactory.CreateProcessRunner();

        await using var handle = await runner.StartAsync(
            Shell, new[] { "/c", "set /p x=" }, MinimalEnv(), CancellationToken.None);

        // Windows has no SIGTERM, so Terminate closes stdin and relies on the
        // child exiting at EOF. If this ever hangs, the graceful path is broken
        // and M3.4 sessions will leak processes.
        await runner.KillAsync(handle, ProcessSignal.Terminate, CancellationToken.None);
        var exit = await runner.WaitAsync(handle, 15_000, CancellationToken.None);

        Assert.False(exit.TimedOut);
    }

    [Fact]
    public async Task Dispose_kills_a_child_that_was_never_waited_on()
    {
        var runner = PlatformFactory.CreateProcessRunner();

        var handle = await runner.StartAsync(
            Shell, SleepArgs(60), MinimalEnv(), CancellationToken.None);
        await handle.DisposeAsync();

        Assert.True(handle.HasExited);
    }

    // -------------------------------------------------------------------------
    // Environment isolation (Sprint 5 rule carried into the session path)
    // -------------------------------------------------------------------------

    [Fact]
    public async Task Start_passes_the_listed_environment_to_the_child()
    {
        var runner = PlatformFactory.CreateProcessRunner();
        var env = MinimalEnv().Environment.ToDictionary(pair => pair.Key, pair => pair.Value);
        env["AGENTBOARD_TEST_VAR"] = "probe-value";

        await using var handle = await runner.StartAsync(
            Shell,
            PrintEnvArgs("AGENTBOARD_TEST_VAR"),
            new ProcessStartOptions { Environment = env },
            CancellationToken.None);
        await runner.WaitAsync(handle, 30_000, CancellationToken.None);

        var output = await runner.ReadOutputAsync(handle, 4096, CancellationToken.None);

        Assert.Contains("[probe-value]", Encoding.UTF8.GetString(output));
    }

    [Fact]
    public async Task Start_does_not_leak_unlisted_environment_into_the_child()
    {
        Environment.SetEnvironmentVariable("AGENTBOARD_TEST_VAR", "probe-value");

        try
        {
            var runner = PlatformFactory.CreateProcessRunner();

            await using var handle = await runner.StartAsync(
                Shell,
                PrintEnvArgs("AGENTBOARD_TEST_VAR"),
                MinimalEnv(),
                CancellationToken.None);
            await runner.WaitAsync(handle, 30_000, CancellationToken.None);

            var text = Encoding.UTF8.GetString(
                await runner.ReadOutputAsync(handle, 4096, CancellationToken.None));

            // The variable is set in this process, so seeing it in the child
            // would mean the runner silently inherited the parent environment.
            Assert.DoesNotContain("probe-value", text);
        }
        finally
        {
            Environment.SetEnvironmentVariable("AGENTBOARD_TEST_VAR", null);
        }
    }

    // -------------------------------------------------------------------------
    // Contract guards
    // -------------------------------------------------------------------------

    [Fact]
    public async Task Start_rejects_a_blank_command()
    {
        var runner = PlatformFactory.CreateProcessRunner();

        await Assert.ThrowsAsync<ArgumentException>(() => runner.StartAsync(
            "   ", Array.Empty<string>(), new ProcessStartOptions(), CancellationToken.None));
    }

    [Fact]
    public async Task A_handle_from_a_different_runner_is_rejected()
    {
        // Mixing handles across platform implementations would silently bypass
        // the platform-specific kill semantics, so it fails loudly instead.
        var runner = PlatformFactory.CreateProcessRunner();
        IProcessRunner other = IsWindows
            ? new UnixProcessRunner()
            : new WindowsProcessRunner();

        await using var handle = await runner.StartAsync(
            Shell, EchoArgs("hello"), MinimalEnv(), CancellationToken.None);

        await Assert.ThrowsAsync<ArgumentException>(
            () => other.WaitAsync(handle, 5_000, CancellationToken.None));
    }

    // -------------------------------------------------------------------------

    private static string[] EchoArgs(string text) => IsWindows
        ? new[] { "/c", "echo", text }
        : new[] { "-c", $"echo {text}" };

    private static string[] SleepArgs(int seconds) => IsWindows
        ? new[] { "/c", $"ping -n {seconds + 1} 127.0.0.1 > nul" }
        : new[] { "-c", $"sleep {seconds}" };

    private static string[] PrintEnvArgs(string name) => IsWindows
        ? new[] { "/c", $"echo [%{name}%]" }
        : new[] { "-c", $"echo [${name}]" };

    /// <summary>
    /// The runner inherits nothing from the parent, so the child needs the few
    /// variables the OS loader itself depends on.
    /// </summary>
    private static ProcessStartOptions MinimalEnv()
    {
        var environment = new Dictionary<string, string?>();
        foreach (var name in new[] { "PATH", "Path", "SYSTEMROOT", "SystemRoot", "TEMP", "TMP" })
        {
            var value = Environment.GetEnvironmentVariable(name);
            if (!string.IsNullOrEmpty(value)) environment[name] = value;
        }

        return new ProcessStartOptions { Environment = environment };
    }
}
