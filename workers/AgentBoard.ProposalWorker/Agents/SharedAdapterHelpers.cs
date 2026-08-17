using System.Text.Json;
using AgentBoard.ProposalWorker.Process;

namespace AgentBoard.ProposalWorker.Agents;

/// <summary>
/// Shared helper for adapters that don't need the workbuddy-specific
/// stdin-only behavior. Runs the process and parses the last JSON object
/// from the tail, identical to the legacy WorkBuddyRunner parsing logic.
/// </summary>
internal static class SharedAdapterHelpers
{
    public static async Task<AgentExecutionResult> RunAndParseAsync(IProcessExecutor process, ProcessSpec spec, CancellationToken ct)
    {
        var result = await process.ExecuteAsync(spec, ct);
        var output = result.RedactedOutput ?? "";
        return new AgentExecutionResult(
            Success: result.ExitCode == 0 && !result.TimedOut && !result.Cancelled,
            OutputJson: TryExtractLastJson(output),
            ErrorMessage: result.Cancelled ? "cancelled"
                : result.TimedOut ? "timeout"
                : result.ExitCode == 0 ? null : $"exit {result.ExitCode}: {result.StderrTail}",
            ExitCode: result.ExitCode,
            Duration: result.Duration,
            TimedOut: result.TimedOut,
            Cancelled: result.Cancelled);
    }

    private static string? TryExtractLastJson(string text)
    {
        for (var i = text.Length - 1; i >= 0; i--)
        {
            if (text[i] != '{') continue;
            var depth = 0;
            for (var j = i; j < text.Length; j++)
            {
                if (text[j] == '{') depth++;
                else if (text[j] == '}')
                {
                    depth--;
                    if (depth == 0)
                    {
                        var slice = text[i..(j + 1)];
                        try { using var _ = JsonDocument.Parse(slice); return slice; }
                        catch { break; }
                    }
                }
            }
        }
        return null;
    }
}
