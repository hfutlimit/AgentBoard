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

            var stdoutTask = process.StandardOutput.ReadToEndAsync(timeoutToken);
            var stderrTask = process.StandardError.ReadToEndAsync(timeoutToken);

            if (!string.IsNullOrEmpty(spec.StdinPayload))
            {
                await process.StandardInput.WriteAsync(spec.StdinPayload.AsMemory(), timeoutToken);
                process.StandardInput.Close();
            }
            else
            {
                process.StandardInput.Close();
            }

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
            catch (OperationCanceledException) // timeout fired
            {
                TryKillTree(process);
                var so = await SafeReadAsync(stdoutTask);
                var se = await SafeReadAsync(stderrTask);
                return new ProcessResult
                {
                    ExitCode = -1,
                    OutputTail = Tail(so, spec.MaxOutputBytes),
                    StderrTail = Tail(se, spec.MaxOutputBytes),
                    Duration = DateTimeOffset.UtcNow - startedAt,
                    TimedOut = true,
                    RedactedOutput = Redact(Tail(so, spec.MaxOutputBytes)),
                };
            }

            var stdout = await SafeReadAsync(stdoutTask);
            var stderr = await SafeReadAsync(stderrTask);
            var combined = Tail(stdout, spec.MaxOutputBytes);
            return new ProcessResult
            {
                ExitCode = process.ExitCode,
                OutputTail = combined,
                StderrTail = Tail(stderr, spec.MaxOutputBytes),
                Duration = DateTimeOffset.UtcNow - startedAt,
                RedactedOutput = Redact(combined),
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

    private static async Task<string> SafeReadAsync(Task<string> t)
    {
        try { return await t; } catch { return ""; }
    }

    private static string Tail(string s, int max) => s.Length <= max ? s : s[^max..];

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
