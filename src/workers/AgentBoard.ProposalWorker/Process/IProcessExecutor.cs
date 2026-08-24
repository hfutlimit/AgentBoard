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

        foreach (var a in spec.Arguments) start.ArgumentList.Add(a);

        using var process = new System.Diagnostics.Process { StartInfo = start };
        var startedAt = DateTimeOffset.UtcNow;
        try
        {
            if (!process.Start())
                return new ProcessResult { ExitCode = -1, OutputTail = "", StderrTail = $"could not start {spec.Executable}", Duration = TimeSpan.Zero };

            var stdoutTask = process.StandardOutput.ReadToEndAsync(ct);
            var stderrTask = process.StandardError.ReadToEndAsync(ct);

            if (!string.IsNullOrEmpty(spec.StdinPayload))
            {
                await process.StandardInput.WriteAsync(spec.StdinPayload.AsMemory(), ct);
                process.StandardInput.Close();
            }
            else
            {
                process.StandardInput.Close();
            }

            using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
            timeoutCts.CancelAfter(spec.Timeout);
            try
            {
                await process.WaitForExitAsync(timeoutCts.Token);
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
}
