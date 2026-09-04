// SPDX-License-Identifier: MIT
namespace AgentBoard.Node.Platform;

/// <summary>
/// How hard to stop a running process.
/// </summary>
/// <remarks>
/// <c>Terminate</c> is a request, <c>Kill</c> is a fact. The two differ per
/// platform: Windows has no SIGTERM at all, so <c>Terminate</c> there means
/// "close the child's stdin and give it a grace period", whereas Unix sends a
/// real SIGTERM. Callers should always escalate Terminate → Kill rather than
/// assume Terminate succeeded.
/// </remarks>
public enum ProcessSignal
{
    Terminate,
    Kill,
}

/// <summary>
/// Options for <see cref="IProcessRunner.StartAsync"/>.
/// </summary>
public sealed record ProcessStartOptions
{
    public string? WorkingDirectory { get; init; }

    /// <summary>
    /// The complete environment of the child. Following the Sprint 5
    /// isolation rule the runner inherits nothing from the parent: a Provider
    /// CLI that needs PATH, HOME or CODEX_HOME must have it listed here.
    /// </summary>
    public IReadOnlyDictionary<string, string?> Environment { get; init; } =
        new Dictionary<string, string?>();

    public bool RedirectStdin { get; init; } = true;
    public bool RedirectStdout { get; init; } = true;
    public bool RedirectStderr { get; init; } = true;
}

/// <summary>
/// Outcome of <see cref="IProcessRunner.WaitAsync"/>.
/// </summary>
/// <param name="ExitCode">Process exit code, or -1 when the wait timed out.</param>
/// <param name="TimedOut">True when the process was still running at the deadline.</param>
public sealed record ProcessExit(int ExitCode, bool TimedOut);

/// <summary>
/// A handle to a running child process.
/// </summary>
/// <remarks>
/// Streams are the raw pipes rather than <see cref="StreamReader"/>s because
/// M4 feeds ExecutionEvent stdout chunks straight into the local SSE stream —
/// decoding is the consumer's choice, not the runner's.
/// </remarks>
public interface IProcessHandle : IAsyncDisposable
{
    long Id { get; }
    bool HasExited { get; }
    int? ExitCode { get; }

    /// <summary>Null when stdin was not redirected.</summary>
    Stream? StandardInput { get; }

    Stream StandardOutput { get; }
    Stream StandardError { get; }
}
