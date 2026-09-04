// SPDX-License-Identifier: MIT
using System.Diagnostics;

namespace AgentBoard.Node.Platform;

/// <summary>
/// Windows implementation of <see cref="IProcessRunner"/> (v4.3 M0.3).
/// </summary>
/// <remarks>
/// Three Windows specifics live here and nowhere else:
/// <list type="bullet">
/// <item><c>.cmd</c> / <c>.bat</c> entry points are wrapped in
/// <c>%ComSpec% /c</c>. CreateProcess will run a batch file implicitly, but
/// argument quoting through that path is unreliable for npm-installed shims
/// (which is what most Provider CLIs actually are), so the wrapping is
/// explicit.</item>
/// <item>There is no SIGTERM. <see cref="ProcessSignal.Terminate"/> therefore
/// closes the child's stdin — Provider CLIs reading a prompt exit on EOF — and
/// escalates to a tree kill if the grace period expires.</item>
/// <item>Teardown kills the whole tree. Windows has no process-group signal, so
/// an orphaned grandchild would otherwise keep the working tree locked after
/// the parent exits.</item>
/// </list>
/// </remarks>
public sealed class WindowsProcessRunner : IProcessRunner
{
    private static readonly TimeSpan TerminateGrace = TimeSpan.FromSeconds(5);

    private static readonly TimeSpan DisposeGrace = TimeSpan.FromSeconds(5);

    public Task<IProcessHandle> StartAsync(
        string command,
        IReadOnlyList<string> args,
        ProcessStartOptions options,
        CancellationToken ct)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(command);
        ArgumentNullException.ThrowIfNull(args);
        ArgumentNullException.ThrowIfNull(options);
        ct.ThrowIfCancellationRequested();

        var (fileName, argv) = WrapForBatch(command, args);

        var startInfo = new ProcessStartInfo
        {
            FileName = fileName,
            WorkingDirectory = string.IsNullOrWhiteSpace(options.WorkingDirectory)
                ? Environment.CurrentDirectory
                : options.WorkingDirectory,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardInput = options.RedirectStdin,
            RedirectStandardOutput = options.RedirectStdout,
            RedirectStandardError = options.RedirectStderr,
        };

        foreach (var argument in argv) startInfo.ArgumentList.Add(argument);

        // Sprint 5 isolation carries over: the child inherits nothing the
        // caller did not list. A Provider that needs PATH / SYSTEMROOT /
        // CODEX_HOME must have it in options.Environment.
        startInfo.Environment.Clear();
        foreach (var (name, value) in options.Environment)
        {
            startInfo.Environment[name] = value;
        }

        // Fully qualified throughout: this assembly owns an AgentBoard.Node.
        // Process namespace (the batch execution layer), which shadows
        // System.Diagnostics.Process in type positions.
        var process = System.Diagnostics.Process.Start(startInfo)
            ?? throw new InvalidOperationException($"Could not start process '{fileName}'.");

        return Task.FromResult<IProcessHandle>(new WindowsProcessHandle(process));
    }

    public async Task KillAsync(IProcessHandle handle, ProcessSignal signal, CancellationToken ct)
    {
        var process = RequireWindows(handle).Process;
        if (process.HasExited) return;

        if (signal == ProcessSignal.Terminate)
        {
            try
            {
                process.StandardInput.Close();
            }
            catch (InvalidOperationException)
            {
                // stdin was not redirected; fall through to the tree kill.
            }

            try
            {
                await process.WaitForExitAsync(ct)
                    .WaitAsync(TerminateGrace, ct)
                    .ConfigureAwait(false);
                return;
            }
            catch (TimeoutException)
            {
                // ignored the graceful request — escalate below.
            }
        }

        process.Kill(entireProcessTree: true);
    }

    public async Task<byte[]> ReadOutputAsync(IProcessHandle handle, int maxBytes, CancellationToken ct)
    {
        if (maxBytes <= 0) return Array.Empty<byte>();

        var process = RequireWindows(handle).Process;
        var buffer = new byte[maxBytes];
        var read = await process.StandardOutput.BaseStream
            .ReadAsync(buffer.AsMemory(), ct)
            .ConfigureAwait(false);

        return read == maxBytes ? buffer : buffer[..read];
    }

    public async Task<ProcessExit> WaitAsync(IProcessHandle handle, int timeoutMs, CancellationToken ct)
    {
        var process = RequireWindows(handle).Process;

        if (timeoutMs <= 0)
        {
            await process.WaitForExitAsync(ct).ConfigureAwait(false);
            return new ProcessExit(process.ExitCode, TimedOut: false);
        }

        try
        {
            await process.WaitForExitAsync(ct)
                .WaitAsync(TimeSpan.FromMilliseconds(timeoutMs), ct)
                .ConfigureAwait(false);
            return new ProcessExit(process.ExitCode, TimedOut: false);
        }
        catch (TimeoutException)
        {
            return new ProcessExit(-1, TimedOut: true);
        }
    }

    private static (string FileName, IReadOnlyList<string> Argv) WrapForBatch(
        string command,
        IReadOnlyList<string> args)
    {
        var extension = Path.GetExtension(command);
        var isBatch = extension.Equals(".cmd", StringComparison.OrdinalIgnoreCase)
            || extension.Equals(".bat", StringComparison.OrdinalIgnoreCase);

        if (!isBatch) return (command, args);

        var comspec = Environment.GetEnvironmentVariable("ComSpec") ?? "cmd.exe";
        var argv = new List<string> { "/c", command };
        argv.AddRange(args);
        return (comspec, argv);
    }

    private static WindowsProcessHandle RequireWindows(IProcessHandle handle)
        => handle as WindowsProcessHandle
           ?? throw new ArgumentException(
               $"Handle was not created by {nameof(WindowsProcessRunner)}.", nameof(handle));

    private sealed class WindowsProcessHandle(System.Diagnostics.Process process) : IProcessHandle
    {
        private bool _disposed;
        private bool _exitedSnapshot;
        private int? _exitCodeSnapshot;

        public System.Diagnostics.Process Process { get; } = process;

        // Captured up front: Process.Id throws once the handle is disposed, and
        // an id that disappears on disposal is useless in a teardown log line.
        public long Id { get; } = process.Id;

        public bool HasExited
        {
            get
            {
                if (_disposed) return _exitedSnapshot;

                try
                {
                    return process.HasExited;
                }
                catch (InvalidOperationException)
                {
                    return true;
                }
            }
        }

        public int? ExitCode
        {
            get
            {
                if (_disposed) return _exitCodeSnapshot;

                try
                {
                    return process.HasExited ? process.ExitCode : null;
                }
                catch (InvalidOperationException)
                {
                    return null;
                }
            }
        }

        public Stream? StandardInput
        {
            get
            {
                try
                {
                    return process.StandardInput.BaseStream;
                }
                catch (InvalidOperationException)
                {
                    return null;
                }
            }
        }

        public Stream StandardOutput => process.StandardOutput.BaseStream;

        public Stream StandardError => process.StandardError.BaseStream;

        public async ValueTask DisposeAsync()
        {
            try
            {
                if (!process.HasExited)
                {
                    process.Kill(entireProcessTree: true);

                    // Kill() returns before the OS has actually reaped the
                    // process. Without waiting, a caller that disposes and then
                    // inspects HasExited or the working tree races against the
                    // teardown — which for M3.4 sessions means a directory that
                    // is still locked.
                    await process.WaitForExitAsync()
                        .WaitAsync(DisposeGrace)
                        .ConfigureAwait(false);
                }
            }
            catch (Exception)
            {
                // best effort: the process may already be gone or outlive the grace.
            }

            // Snapshot before releasing the handle. After Dispose() the Process
            // object throws "No process is associated with this object" on every
            // state read, so without this a harmless post-dispose log line
            // inside a finally block would throw.
            try
            {
                _exitedSnapshot = process.HasExited;
            }
            catch (Exception)
            {
                _exitedSnapshot = true;
            }

            try
            {
                _exitCodeSnapshot = _exitedSnapshot ? process.ExitCode : null;
            }
            catch (Exception)
            {
                _exitCodeSnapshot = null;
            }

            _disposed = true;
            process.Dispose();
        }
    }
}
