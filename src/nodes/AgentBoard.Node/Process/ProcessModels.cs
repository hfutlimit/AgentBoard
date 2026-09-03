namespace AgentBoard.Node.Process;

/// <summary>
/// Declarative spec for spawning an agent CLI. The adapter constructs one
/// of these; <see cref="ProcessExecutor"/> owns the actual process lifecycle.
/// </summary>
public sealed record ProcessSpec
{
    public required string Executable { get; init; }
    public string[] Arguments { get; init; } = Array.Empty<string>();
    public string? WorkingDirectory { get; init; }
    /// <summary>
    /// Explicit env for the child. Worker constructs this from a small
    /// allow-list + adapter-supplied business vars. <see cref="ProcessExecutor"/>
    /// MUST NOT inherit from the parent shell — that's the whole point of
    /// the env-isolation fix in Sprint 5.
    /// </summary>
    public IDictionary<string, string?> Environment { get; init; } = new Dictionary<string, string?>();
    public string? StdinPayload { get; init; }
    public TimeSpan Timeout { get; init; } = TimeSpan.FromMinutes(30);
    public TimeSpan KillGrace { get; init; } = TimeSpan.FromSeconds(5);
    public int MaxOutputBytes { get; init; } = 100 * 1024;
    /// <summary>
    /// Optional file path the executor streams full stdout/stderr to.
    /// When null, executor falls back to <c>execution_logs</c> in Sprint 6.
    /// </summary>
    public string? FullLogPath { get; init; }
    /// <summary>For log attribution; defaults to "system" when not set.</summary>
    public string AgentType { get; init; } = "system";
}

public sealed record ProcessResult
{
    public int ExitCode { get; init; }
    /// <summary>Last <c>MaxOutputBytes</c> of stdout (combined with stderr per spec).</summary>
    public string OutputTail { get; init; } = "";
    public string StderrTail { get; init; } = "";
    public string? FullLogPath { get; init; }
    public TimeSpan Duration { get; init; }
    public bool TimedOut { get; init; }
    public bool Cancelled { get; init; }
    public string? RedactedOutput { get; init; }
}
