using System.Diagnostics;
using Microsoft.Extensions.Options;

namespace AgentBoard.ProposalWorker.Process;

public interface IProcessExecutor
{
    Task<ProcessResult> ExecuteAsync(ProcessSpec spec, CancellationToken ct);
}

/// <summary>
/// Sprint 5 (skeleton). Single owner of agent process lifecycle. All three
/// IAgentAdapter implementations route through this.
///
/// Implementation status (this turn): timeout + cancel + tail buffer + env
/// isolation are wired. Redaction is regex-based and applied to the tail that
/// the adapter sees. Sprint 5 follow-up: full log to file + per-line redaction.
/// </summary>
public sealed class ProcessExecutor : IProcessExecutor
{
    private static readonly System.Text.RegularExpressions.Regex[] Redactors =
    {
        new(@"(?i)(openai|anthropic|minimax|codex)[_-]?api[_-]?key\s*[=:]\s*\S+", System.Text.RegularExpressions.RegexOptions.Compiled),
        new(@"Authorization:\s*Bearer\s+\S+", System.Text.RegularExpressions.RegexOptions.Compiled),
    };

    public async Task<ProcessResult> ExecuteAsync(ProcessSpec spec, CancellationToken ct)
    {
        if (string.IsNullOrWhiteSpace(spec.Executable))
            return new ProcessResult { ExitCode = -1, OutputTail = "", StderrTail = "executable not configured", Duration = TimeSpan.Zero };

        var start = new ProcessStartInfo
        {
            FileName = spec.Executable,
            WorkingDirectory = string.IsNullOrWhiteSpace(spec.WorkingDirectory) ? Environment.CurrentDirectory : spec.WorkingDirectory,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        // Sprint 5: env-isolation fix. We COPY from parent only the vars the
        // spec explicitly lists; everything else stays out by default. The
        // adapter MUST set every env var it needs in spec.Environment.
        start.Environment.Clear();
        foreach (var (k, v) in spec.Environment)
        {
            if (v is not null) start.Environment[k] = v;
        }

        // Windows: npm-global installs ship CLI wrappers as .cmd files
        // (e.g. minimax.cmd, codex.cmd). Process.Start does not resolve
        // .cmd/.bat directly — it raises Win32Exception 193. Wrap with
        // cmd.exe so the OS can apply PATHEXT semantics transparently.
        // See docs/workbuddy-cli-integration.md and
        // docs/minimax-code-integration.md for the original analysis.
        if (OperatingSystem.IsWindows() &&
            NeedsCmdWrapper(spec.Executable, out var wrappedPath))
        {
            start.FileName = "cmd.exe";
            start.ArgumentList.Clear();
            start.ArgumentList.Add("/c");
            start.ArgumentList.Add(wrappedPath);
            foreach (var a in spec.Arguments) start.ArgumentList.Add(a);
        }
        else
        {
            foreach (var a in spec.Arguments) start.ArgumentList.Add(a);
        }

        using var process = new System.Diagnostics.Process { StartInfo = start };
        var startedAt = DateTimeOffset.UtcNow;

        // Build the linked timeout CTS BEFORE starting the process so that
        // stdin write + stdout read + WaitForExitAsync all share the same
        // budget. The previous version created it after stdin write, which
        // meant a CLI that never consumed its stdin pipe could block
        // WriteAsync indefinitely even when TimeoutSeconds was set (#8 in
        // the 2026-08-28 review).
        using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeoutCts.CancelAfter(spec.Timeout);
        var timeoutToken = timeoutCts.Token;

        try
        {
            if (!process.Start())
                return new ProcessResult { ExitCode = -1, OutputTail = "", StderrTail = $"could not start {spec.Executable}", Duration = TimeSpan.Zero };

            // 2026-08-29 review follow-up: the previous ReadToEndAsync
            // buffered the full stdout/stderr in memory and only
            // truncated to MaxOutputBytes post-mortem. A runaway CLI
            // (e.g. an agent stuck in a log-spam loop) would OOM the
            // worker. The new design reads the underlying stream in
            // 8 KB chunks into a BoundedByteQueue that keeps at most
            // MaxOutputBytes at any time. Memory is bounded
            // independently of how much the CLI actually emits.
            var stdoutSink = new BoundedByteQueue(spec.MaxOutputBytes);
            var stderrSink = new BoundedByteQueue(spec.MaxOutputBytes);
            var stdoutTask = ReadStreamBoundedAsync(process.StandardOutput.BaseStream, stdoutSink, timeoutToken);
            var stderrTask = ReadStreamBoundedAsync(process.StandardError.BaseStream, stderrSink, timeoutToken);

            // stdin write + WaitForExit + post-mortem stdout read all live
            // inside the SAME try block so the OperationCanceledException
            // classification below applies to every step. The previous
            // version split stdin write into the outer try, where it
            // surfaced as a generic Exception; that meant a CLI that never
            // drained its stdin pipe (so WriteAsync blocked until the
            // shared timeout CTS fired) was mis-classified as a plain
            // `Failed` instead of `TimedOut`. Fix for #5 in the 2026-08-28
            // review: caller cancel vs. timeout CTS are still distinguished
            // by inspecting `ct.IsCancellationRequested` first.
            try
            {
                if (!string.IsNullOrEmpty(spec.StdinPayload))
                {
                    await process.StandardInput.WriteAsync(spec.StdinPayload.AsMemory(), timeoutToken);
                }
                process.StandardInput.Close();

                try
                {
                    await process.WaitForExitAsync(timeoutToken);
                }
                catch (OperationCanceledException) when (ct.IsCancellationRequested)
                {
                    TryKillTree(process);
                    return new ProcessResult
                    {
                        ExitCode = -1,
                        OutputTail = "",
                        StderrTail = "cancelled by caller",
                        Duration = DateTimeOffset.UtcNow - startedAt,
                        Cancelled = true,
                        RedactedOutput = "",
                    };
                }
                catch (OperationCanceledException) // timeout fired (or stdin-write timed out)
                {
                    TryKillTree(process);
                    // 2026-08-29 review follow-up: the background reader
                    // tasks are still running when we reach this catch
                    // (the timeout CTS may have just fired). Calling
                    // GetText() while a reader is mid-Append can race
                    // the Queue<T> enumeration and raise
                    // InvalidOperationException, which would then
                    // surface as a generic `Failed` result here. Drain
                    // the readers first — after TryKillTree the OS
                    // pipe EOFs and the read tasks complete quickly.
                    try { await stdoutTask; } catch { /* may OCE on cancel */ }
                    try { await stderrTask; } catch { /* may OCE on cancel */ }
                    return new ProcessResult
                    {
                        ExitCode = -1,
                        OutputTail = stdoutSink.GetText(),
                        StderrTail = Redact(stderrSink.GetText()),
                        Duration = DateTimeOffset.UtcNow - startedAt,
                        TimedOut = true,
                        RedactedOutput = Redact(stdoutSink.GetText()),
                    };
                }
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested)
            {
                TryKillTree(process);
                return new ProcessResult
                {
                    ExitCode = -1,
                    OutputTail = "",
                    StderrTail = "cancelled by caller (stdin write)",
                    Duration = DateTimeOffset.UtcNow - startedAt,
                    Cancelled = true,
                    RedactedOutput = "",
                };
            }
            catch (OperationCanceledException) // timeout fired during stdin write
            {
                TryKillTree(process);
                // Same drain as the WaitForExit-timeout path above;
                // a concurrent reader + GetText would race the
                // BoundedByteQueue's chunk queue.
                try { await stdoutTask; } catch { /* may OCE on cancel */ }
                try { await stderrTask; } catch { /* may OCE on cancel */ }
                return new ProcessResult
                {
                    ExitCode = -1,
                    OutputTail = stdoutSink.GetText(),
                    StderrTail = Redact(stderrSink.GetText()),
                    Duration = DateTimeOffset.UtcNow - startedAt,
                    TimedOut = true,
                    RedactedOutput = Redact(stdoutSink.GetText()),
                };
            }

            // Process exited normally (or wait returned without cancel).
            // The streams are EOF'd; await the read tasks to drain
            // whatever is still buffered in the OS pipe. If the process
            // exited cleanly the tasks have already completed.
            try { await stdoutTask; } catch { /* may throw on cancel */ }
            try { await stderrTask; } catch { /* may throw on cancel */ }
            var stdout = stdoutSink.GetText();
            var stderr = stderrSink.GetText();
            return new ProcessResult
            {
                ExitCode = process.ExitCode,
                OutputTail = stdout,
                StderrTail = Redact(stderr),
                Duration = DateTimeOffset.UtcNow - startedAt,
                RedactedOutput = Redact(stdout),
            };
        }
        catch (Exception ex)
        {
            TryKillTree(process);
            return new ProcessResult
            {
                ExitCode = -1,
                OutputTail = "",
                StderrTail = ex.Message,
                Duration = DateTimeOffset.UtcNow - startedAt,
            };
        }
    }

    /// <summary>
    /// Stream <paramref name="stream"/> into <paramref name="sink"/> in
    /// 8 KB chunks. The sink enforces the byte cap; this method does
    /// no per-chunk allocation beyond the 8 KB scratch buffer. Exits
    /// cleanly on EOF, cancellation, or stream-closed (the latter
    /// surfaces as <see cref="System.IO.IOException"/> when the
    /// process is killed before it flushes).
    /// </summary>
    private static async Task ReadStreamBoundedAsync(
        Stream stream, BoundedByteQueue sink, CancellationToken ct)
    {
        var buffer = new byte[8192];
        try
        {
            while (!ct.IsCancellationRequested)
            {
                int n = await stream.ReadAsync(buffer.AsMemory(0, buffer.Length), ct);
                if (n == 0) return;  // EOF
                sink.Append(new ReadOnlySpan<byte>(buffer, 0, n));
            }
        }
        catch (OperationCanceledException) { /* timeout or caller cancel */ }
        catch (System.IO.IOException) { /* stream closed by process kill */ }
        catch (System.Text.DecoderFallbackException) { /* invalid UTF-8 mid-stream — keep what we have */ }
    }

    private static string Redact(string s)
    {
        foreach (var rx in Redactors) s = rx.Replace(s, "***REDACTED***");
        return s;
    }

    private static void TryKillTree(System.Diagnostics.Process p)
    {
        try { if (!p.HasExited) p.Kill(entireProcessTree: true); } catch { /* best-effort */ }
    }

    /// <summary>
    /// True when <paramref name="executable"/> points at a Windows .cmd or
    /// .bat wrapper. Bare names (e.g. <c>codex</c>) are not wrapped here —
    /// the caller is expected to have resolved them via <see cref="Agents.CliLocator"/>
    /// so we know the real path. We do an extension check only; the
    /// alternative would be a full <c>where.exe</c> probe per spawn, which
    /// is too expensive on the hot path.
    /// </summary>
    private static bool NeedsCmdWrapper(string executable, out string fullPath)
    {
        fullPath = executable;
        if (!OperatingSystem.IsWindows()) return false;
        // Absolute or relative path with a .cmd / .bat extension — wrap.
        if (executable.EndsWith(".cmd", StringComparison.OrdinalIgnoreCase) ||
            executable.EndsWith(".bat", StringComparison.OrdinalIgnoreCase))
        {
            return true;
        }
        // Bare name without an extension and without slashes — assume it is a
        // .cmd wrapper. The CliLocator returns absolute paths for known
        // installs; PATHEXT-driven resolution happens in the OS layer
        // (cmd.exe honours PATHEXT) so this is the safe default.
        if (executable.IndexOfAny(new[] { '/', '\\' }) < 0 &&
            !Path.HasExtension(executable))
        {
            return true;
        }
        return false;
    }
}
