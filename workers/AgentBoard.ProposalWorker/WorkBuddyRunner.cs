using System.Diagnostics;
using Microsoft.Extensions.Options;

namespace AgentBoard.ProposalWorker;

public sealed class WorkBuddyRunner
{
    private readonly WorkBuddyOptions _options;
    private readonly WorkerOptions _worker;
    public WorkBuddyRunner(IOptions<WorkBuddyOptions> options, IOptions<WorkerOptions> worker) { _options = options.Value; _worker = worker.Value; }

    public async Task<(int ExitCode, string Output, string? Error)> RunAsync(ProposalMessage message, CancellationToken ct)
    {
        if (string.IsNullOrWhiteSpace(_options.Command)) return (-1, "", "WorkBuddy:Command is not configured");
        var start = new ProcessStartInfo { FileName = _options.Command, WorkingDirectory = string.IsNullOrWhiteSpace(_options.WorkingDirectory) ? Environment.CurrentDirectory : _options.WorkingDirectory, RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true, UseShellExecute = false, CreateNoWindow = true };
        using var process = new Process { StartInfo = start };
        try
        {
            if (!process.Start()) return (-1, "", "could not start WorkBuddy");
            var stdout = process.StandardOutput.ReadToEndAsync(ct); var stderr = process.StandardError.ReadToEndAsync(ct);
            await process.StandardInput.WriteAsync(BuildPrompt(message)); process.StandardInput.Close();
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct); timeout.CancelAfter(TimeSpan.FromMinutes(Math.Max(1, _options.TimeoutMinutes)));
            await process.WaitForExitAsync(timeout.Token);
            var output = Limit((await stdout) + Environment.NewLine + (await stderr));
            return (process.ExitCode, output, process.ExitCode == 0 ? null : "WorkBuddy exited with code " + process.ExitCode);
        }
        catch (OperationCanceledException) { TryKill(process); return (-1, "", "WorkBuddy timed out or was cancelled"); }
        catch (Exception ex) { TryKill(process); return (-1, "", ex.Message); }
    }

    private string BuildPrompt(ProposalMessage message) => $"""
        You are the AgentBoard proposal worker. Use your already configured AgentBoard MCP only; do not access AgentBoard databases directly.
        Handle proposal {message.ProposalId}, round {message.Round}, reason '{message.Reason}', on worker '{_worker.Id}'.
        Claim/read the proposal through MCP, reconstruct its complete question-answer history, and determine the next action.
        If clarification is needed, write concrete open questions through MCP. If it is converged, write the converged proposal through MCP. Record failures through MCP when appropriate.
        This is an unattended worker: make no destructive local changes unless the proposal explicitly asks for them and MCP confirms the project scope.
        """;
    private string Limit(string text) => text.Length <= _options.MaxCapturedOutputChars ? text : text[^_options.MaxCapturedOutputChars..];
    private static void TryKill(Process process) { try { if (!process.HasExited) process.Kill(true); } catch { } }
}
