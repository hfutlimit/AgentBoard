// SPDX-License-Identifier: MIT
using System.Diagnostics;
using System.Runtime.InteropServices;

namespace AgentBoard.Node.Platform;

/// <summary>
/// Unix implementation of <see cref="IProcessRunner"/>, used for the macOS
/// target (v4.3 M0.3).
/// </summary>
/// <remarks>
/// <para>
/// Named for the semantics it implements rather than the single OS it serves:
/// v4.3 has no Linux target, so macOS is the only consumer, but every decision
/// here is generic Unix (signals, direct exec, no batch-file shims). Naming it
/// <c>MacOsProcessRunner</c> would imply macOS-specific behaviour that is not
/// actually there.
/// </para>
/// <para>
/// The one real asymmetry against Windows is <see cref="ProcessSignal.Terminate"/>:
/// Unix can send a genuine SIGTERM, so polite shutdown does not depend on the
/// child happening to exit on stdin EOF. Note that the signal goes to the direct
/// child only — macOS has no <c>setsid</c>, so we cannot put the child in its own
/// process group without a pre-exec hook, and grandchildren may therefore
/// survive a graceful stop. <see cref="ProcessSignal.Kill"/> does kill the tree
/// and is the reliable path. This is on the M7.5 real-machine checklist.
/// </para>
/// </remarks>
public sealed class UnixProcessRunner : IProcessRunner
{
    private const int SigTerm = 15;

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

        // No batch wrapping on Unix: npm shims and Homebrew binaries are
        // ordinary executables with a shebang, and exec'ing them directly keeps
        // argv quoting intact.
        var startInfo = new ProcessStartInfo
        {
            FileName = command,
            WorkingDirectory = string.IsNullOrWhiteSpace(options.WorkingDirectory)
                ? Environment.CurrentDirectory
                : options.WorkingDirectory,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardInput = options.RedirectStdin,
            RedirectStandardOutput = options.RedirectStdout,
            RedirectStandardError = options.RedirectStderr,
        };

        foreach (var argument in args) startInfo.ArgumentList.Add(argument);

        startInfo.Environment.Clear();
        foreach (var (name, value) in options.Environment)
        {
            startInfo.Environment[name] = value;
        }

        // Fully qualified: AgentBoard.Node.Process (the batch execution layer)
        // shadows System.Diagnostics.Process in type positions.
        var process = System.Diagnostics.Process.Start(startInfo)
            ?? throw new InvalidOperationException($"Could not start process '{command}'.");

        return Task.FromResult<IProcessHandle>(new UnixProcessHandle(process));
    }

    public async Task KillAsync(IProcessHandle handle, ProcessSignal signal, CancellationToken ct)
    {
        var process = RequireUnix(handle).Process;
        if (process.HasExited) return;

        if (signal == ProcessSignal.Terminate)
        {
            if (OperatingSystem.IsMacOS() || OperatingSystem.IsLinux())
            {
                try
                {
                    kill((int)process.Id, SigTerm);
                }
                catch (Exception)
                {
                    // P/Invoke unavailable; fall through to the hard kill.
                }
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
                // ignored the signal — escalate below.
            }
        }

        process.Kill(entireProcessTree: true);
    }

    public async Task<byte[]> ReadOutputAsync(IProcessHandle handle, int maxBytes, CancellationToken ct)
    {
        if (maxBytes <= 0) return Array.Empty<byte>();

        var process = RequireUnix(handle).Process;
        var buffer = new byte[maxBytes];
        var read = await process.StandardOutput.BaseStream
            .ReadAsync(buffer.AsMemory(), ct)
            .ConfigureAwait(false);

        return read == maxBytes ? buffer : buffer[..read];
    }

    public async Task<ProcessExit> WaitAsync(IProcessHandle handle, int timeoutMs, CancellationToken ct)
    {
        var process = RequireUnix(handle).Process;

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

    [DllImport("libc", SetLastError = true)]
    private static extern int kill(int pid, int sig);

    private static UnixProcessHandle RequireUnix(IProcessHandle handle)
        => handle as UnixProcessHandle
           ?? throw new ArgumentException(
               $"Handle was not created by {nameof(UnixProcessRunner)}.", nameof(handle));

    private sealed class UnixProcessHandle(System.Diagnostics.Process process) : IProcessHandle
    {
        private bool _disposed;
        private bool _exitedSnapshot;
        private int? _exitCodeSnapshot;

        public System.Diagnostics.Process Process { get; } = process;

        // Captured up front: Process.Id throws once the handle is disposed.
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
                    // process; waiting keeps disposal deterministic for callers
                    // that inspect HasExited or the working tree straight after.
                    await process.WaitForExitAsync()
                        .WaitAsync(DisposeGrace)
                        .ConfigureAwait(false);
                }
            }
            catch (Exception)
            {
                // best effort: the process may already be gone or outlive the grace.
            }

            // Snapshot before releasing the handle: after Dispose() every state
            // read on the Process object throws.
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
