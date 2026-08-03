namespace AgentBoard.ProposalWorker;

public sealed class WorkerState
{
    private readonly object _gate = new();
    private bool _paused;
    private bool _busy;
    private string? _current;
    public DateTimeOffset StartedAt { get; } = DateTimeOffset.UtcNow;
    public DateTimeOffset LastHeartbeatAt { get; set; } = DateTimeOffset.MinValue;
    public string? LastError { get; set; }
    public bool Paused { get { lock (_gate) return _paused; } set { lock (_gate) _paused = value; } }
    public bool Busy { get { lock (_gate) return _busy; } }
    public void Begin(string current) { lock (_gate) { _busy = true; _current = current; } }
    public void End() { lock (_gate) { _busy = false; _current = null; } }
    public object Snapshot() { lock (_gate) return new { healthy = true, busy = _busy, paused = _paused, current = _current, startedAt = StartedAt, lastHeartbeatAt = LastHeartbeatAt, lastError = LastError }; }
}
