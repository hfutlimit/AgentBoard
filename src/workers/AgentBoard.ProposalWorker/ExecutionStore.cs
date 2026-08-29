using Microsoft.Data.Sqlite;
using Microsoft.Extensions.Options;

namespace AgentBoard.ProposalWorker;

/// <summary>
/// Sprint 1 + Sprint 6. Owns the <c>executions</c> table with a full state
/// machine. Every terminal write is a single SQL <c>UPDATE ... WHERE id=? AND
/// status IN (non-terminal)</c> so two writers cannot race a single execution
/// to two different terminal states.
///
/// Also owns the <c>execution_logs</c> table (Sprint 6) for full stdout/stderr
/// capture with redaction. The <c>worker_execution_inbox</c> table is owned
/// by <see cref="InboxStore"/> because it has its own state machine.
/// </summary>
public sealed class ExecutionStore
{
    private readonly string _connectionString;
    private readonly ILogger<ExecutionStore> _log;

    public ExecutionStore(IOptions<WorkerOptions> options, ILogger<ExecutionStore> log)
    {
        var path = Path.GetFullPath(options.Value.HistoryDatabasePath);
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        // DefaultTimeout=1 (1s) → busy_timeout=1000ms. Microsoft.Data.Sqlite
        // auto-retries BUSY at the driver level for the connection's busy_timeout
        // window before surfacing the SqliteException. The default is 30s which
        // would silently bypass ExecutionCoordinator's TryPersistTerminalAsync
        // retry helper (it never sees BUSY because the driver waits it out).
        // 1s is the smallest non-zero DefaultTimeout (the driver treats 0 as
        // "no timeout" / wait-forever — confirmed by direct probe on
        // Microsoft.Data.Sqlite 10.0.0). Combined with the helper's 1.6s
        // inter-attempt delay budget, total transient-wait per terminal
        // write is ~5.6s; a MarkSucceeded + MarkDegraded round is ~11.2s.
        // That is the explicit "transient SQLite lock" wait policy; longer
        // contention surfaces as Degraded (preserving the agent's business
        // result) and the operator reconciles via the inbox completed state.
        _connectionString = new SqliteConnectionStringBuilder
        {
            DataSource = path,
            DefaultTimeout = 1,
        }.ToString();
        _log = log;
        Initialize();
    }

    public string ConnectionString => _connectionString;

    private void Initialize()
    {
        using var connection = new SqliteConnection(_connectionString);
        connection.Open();
        using var command = connection.CreateCommand();
        command.CommandText = """
            CREATE TABLE IF NOT EXISTS executions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              execution_key TEXT NOT NULL,
              workload_type TEXT NOT NULL,
              workload_id INTEGER NOT NULL,
              agent_type TEXT NOT NULL,
              round INTEGER NOT NULL DEFAULT 0,
              reason TEXT NOT NULL DEFAULT '',
              source TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL,
              started_at TEXT NOT NULL,
              finished_at TEXT NULL,
              exit_code INTEGER NULL,
              output TEXT NOT NULL DEFAULT '',
              error TEXT NULL,
              failure_reason TEXT NULL,
              error_stack TEXT NULL,
              payload TEXT NOT NULL,
              retry_requested INTEGER NOT NULL DEFAULT 0,
              UNIQUE(execution_key)
            );
            CREATE INDEX IF NOT EXISTS ix_executions_started_at ON executions(started_at DESC);
            CREATE INDEX IF NOT EXISTS ix_executions_agent ON executions(agent_type, started_at DESC);
            CREATE INDEX IF NOT EXISTS ix_executions_status ON executions(status);

            CREATE TABLE IF NOT EXISTS execution_logs (
              execution_id INTEGER NOT NULL,
              sequence INTEGER NOT NULL,
              stream TEXT NOT NULL,
              agent_type TEXT NOT NULL,
              content TEXT NOT NULL,
              created_at TEXT NOT NULL,
              PRIMARY KEY (execution_id, sequence)
            );
            CREATE INDEX IF NOT EXISTS ix_execution_logs_agent ON execution_logs(agent_type, created_at DESC);
            """;
        command.ExecuteNonQuery();
    }

    // -------------------------------------------------------------------------
    // Sprint 1: state-machine writes
    // -------------------------------------------------------------------------

    /// <summary>Insert a new execution row in Running state. Returns its id.</summary>
    public async Task<long> StartAsync(ExecutionRequest request, string source, CancellationToken ct)
    {
        await using var connection = new SqliteConnection(_connectionString);
        await connection.OpenAsync(ct);
        await using var cmd = connection.CreateCommand();
        cmd.CommandText = """
            INSERT INTO executions(execution_key, workload_type, workload_id, agent_type, round, source, status, started_at, payload)
            VALUES($key,$wtype,$wid,$agent,$round,$source,'Running',$at,$payload);
            SELECT last_insert_rowid();
            """;
        cmd.Parameters.AddWithValue("$key", request.ExecutionKey);
        cmd.Parameters.AddWithValue("$wtype", request.WorkloadType);
        cmd.Parameters.AddWithValue("$wid", request.WorkloadId);
        cmd.Parameters.AddWithValue("$agent", request.AgentType);
        cmd.Parameters.AddWithValue("$round", request.Round);
        cmd.Parameters.AddWithValue("$source", source);
        cmd.Parameters.AddWithValue("$at", DateTimeOffset.UtcNow.ToString("O"));
        cmd.Parameters.AddWithValue("$payload", request.PayloadJson);
        return (long)(await cmd.ExecuteScalarAsync(ct) ?? 0L);
    }

    /// <summary>
    /// CAS terminal write: succeeds only if execution is still in a non-terminal
    /// state. Returns true if the row was updated. Concurrent Mark* calls
    /// cannot both succeed.
    /// </summary>
    private async Task<bool> MarkTerminalAsync(long id, ExecutionState terminal, int? exitCode, string output, string? error, string? failureReason, string? errorStack, CancellationToken ct)
    {
        await using var connection = new SqliteConnection(_connectionString);
        await connection.OpenAsync(ct);
        await using var cmd = connection.CreateCommand();
        cmd.CommandText = """
            UPDATE executions
            SET status=$status, finished_at=$at, exit_code=$code, output=$output,
                error=$error, failure_reason=$reason, error_stack=$stack
            WHERE id=$id AND status IN ('Pending','Claimed','Starting','Running')
            """;
        cmd.Parameters.AddWithValue("$status", terminal.ToString());
        cmd.Parameters.AddWithValue("$at", DateTimeOffset.UtcNow.ToString("O"));
        cmd.Parameters.AddWithValue("$code", (object?)exitCode ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$output", output);
        cmd.Parameters.AddWithValue("$error", (object?)error ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$reason", (object?)failureReason ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$stack", (object?)errorStack ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$id", id);
        return await cmd.ExecuteNonQueryAsync(ct) == 1;
    }

    public Task<bool> MarkSucceededAsync(long id, int exitCode, string output, CancellationToken ct) =>
        MarkTerminalAsync(id, ExecutionState.Succeeded, exitCode, output, null, null, null, ct);

    public Task<bool> MarkFailedAsync(long id, int? exitCode, string output, string error, string? stackTrace, CancellationToken ct) =>
        MarkTerminalAsync(id, ExecutionState.Failed, exitCode, output, error, "exception", stackTrace, ct);

    public Task<bool> MarkTimedOutAsync(long id, string error, string output, CancellationToken ct) =>
        MarkTerminalAsync(id, ExecutionState.TimedOut, null, output, error, "timeout", null, ct);

    public Task<bool> MarkCancelledAsync(long id, string reason, CancellationToken ct) =>
        MarkTerminalAsync(id, ExecutionState.Cancelled, null, "", reason, "cancelled", null, ct);

    /// <summary>
    /// Last-resort terminal write used when the agent's business
    /// result is known but the normal terminal CAS writes all
    /// exhausted their retries. The row settles to <c>Degraded</c>
    /// with the original business result preserved in the
    /// <c>error</c> field (e.g. "Succeeded; persistence retries
    /// exhausted") and a <see cref="ExecutionState.Degraded"/>
    /// status that the operator can filter on in
    /// <c>WorkerState.Snapshot</c>. The inbox is still marked
    /// completed by the caller so the dispatcher does not retry
    /// the agent.
    /// </summary>
    public Task<bool> MarkDegradedAsync(long id, string note, CancellationToken ct) =>
        MarkTerminalAsync(id, ExecutionState.Degraded, null, "", note, "persistence", null, ct);

    /// <summary>
    /// Sprint 1 orphan detection. Runs once at startup. Any execution that is
    /// still in a non-terminal state past the threshold is a crashed worker;
    /// mark it TimedOut so it doesn't sit in Running forever.
    /// </summary>
    public async Task<int> MarkOrphansAsync(int thresholdMinutes, CancellationToken ct)
    {
        await using var connection = new SqliteConnection(_connectionString);
        await connection.OpenAsync(ct);
        await using var cmd = connection.CreateCommand();
        var cutoff = DateTimeOffset.UtcNow.AddMinutes(-thresholdMinutes).ToString("O");
        cmd.CommandText = """
            UPDATE executions
            SET status='TimedOut', finished_at=$at, failure_reason='orphaned', error_stack='orphaned by startup recovery'
            WHERE status IN ('Claimed','Starting','Running') AND started_at < $cutoff
            """;
        cmd.Parameters.AddWithValue("$at", DateTimeOffset.UtcNow.ToString("O"));
        cmd.Parameters.AddWithValue("$cutoff", cutoff);
        var n = await cmd.ExecuteNonQueryAsync(ct);
        if (n > 0) _log.LogWarning("Marked {Count} orphaned executions as TimedOut (cutoff={Cutoff})", n, cutoff);
        return n;
    }

    // -------------------------------------------------------------------------
    // Read APIs (unchanged shape from before; field count grew)
    // -------------------------------------------------------------------------

    public async Task<IReadOnlyList<ExecutionRecord>> ListAsync(int limit, string? agentType = null, CancellationToken ct = default)
    {
        var list = new List<ExecutionRecord>();
        await using var c = new SqliteConnection(_connectionString);
        await c.OpenAsync(ct);
        await using var cmd = c.CreateCommand();
        cmd.CommandText = agentType is null
            ? "SELECT id,workload_id,workload_type,round,reason,source,agent_type,status,started_at,finished_at,exit_code,output,error,failure_reason,error_stack,payload FROM executions ORDER BY id DESC LIMIT $limit"
            : "SELECT id,workload_id,workload_type,round,reason,source,agent_type,status,started_at,finished_at,exit_code,output,error,failure_reason,error_stack,payload FROM executions WHERE agent_type=$agent ORDER BY id DESC LIMIT $limit";
        cmd.Parameters.AddWithValue("$limit", limit);
        if (agentType is not null) cmd.Parameters.AddWithValue("$agent", agentType);
        await using var rows = await cmd.ExecuteReaderAsync(ct);
        while (await rows.ReadAsync(ct)) list.Add(Read(rows));
        return list;
    }

    public async Task<ExecutionRecord?> GetAsync(long id, CancellationToken ct = default)
    {
        await using var c = new SqliteConnection(_connectionString);
        await c.OpenAsync(ct);
        await using var cmd = c.CreateCommand();
        cmd.CommandText = "SELECT id,workload_id,workload_type,round,reason,source,agent_type,status,started_at,finished_at,exit_code,output,error,failure_reason,error_stack,payload FROM executions WHERE id=$id";
        cmd.Parameters.AddWithValue("$id", id);
        await using var rows = await cmd.ExecuteReaderAsync(ct);
        return await rows.ReadAsync(ct) ? Read(rows) : null;
    }

    /// <summary>
    /// Look up an existing execution row by its stable <c>execution_key</c>.
    /// Used by the Coordinator to resolve UNIQUE collisions during crash
    /// recovery (fix for #4 in the 2026-08-28 review).
    /// </summary>
    public async Task<ExecutionRecord?> GetByKeyAsync(string executionKey, CancellationToken ct = default)
    {
        await using var c = new SqliteConnection(_connectionString);
        await c.OpenAsync(ct);
        await using var cmd = c.CreateCommand();
        cmd.CommandText = "SELECT id,workload_id,workload_type,round,reason,source,agent_type,status,started_at,finished_at,exit_code,output,error,failure_reason,error_stack,payload FROM executions WHERE execution_key=$key";
        cmd.Parameters.AddWithValue("$key", executionKey);
        await using var rows = await cmd.ExecuteReaderAsync(ct);
        return await rows.ReadAsync(ct) ? Read(rows) : null;
    }

    public async Task<bool> QueueRetryAsync(long id, CancellationToken ct = default)
    {
        await using var c = new SqliteConnection(_connectionString);
        await c.OpenAsync(ct);
        await using var cmd = c.CreateCommand();
        cmd.CommandText = "UPDATE executions SET retry_requested=1 WHERE id=$id AND status IN ('Failed','TimedOut','Cancelled')";
        cmd.Parameters.AddWithValue("$id", id);
        return await cmd.ExecuteNonQueryAsync(ct) == 1;
    }

    // -------------------------------------------------------------------------
    // Sprint 6: per-execution full log storage
    // -------------------------------------------------------------------------

    public async Task AppendLogAsync(long executionId, string stream, string agentType, string content, CancellationToken ct = default)
    {
        if (string.IsNullOrEmpty(content)) return;
        await using var c = new SqliteConnection(_connectionString);
        await c.OpenAsync(ct);
        await using var cmd = c.CreateCommand();
        cmd.CommandText = """
            INSERT INTO execution_logs(execution_id, sequence, stream, agent_type, content, created_at)
            SELECT $eid, COALESCE(MAX(sequence),0)+1, $stream, $agent, $content, $at FROM execution_logs WHERE execution_id=$eid
            """;
        cmd.Parameters.AddWithValue("$eid", executionId);
        cmd.Parameters.AddWithValue("$stream", stream);
        cmd.Parameters.AddWithValue("$agent", agentType);
        cmd.Parameters.AddWithValue("$content", content);
        cmd.Parameters.AddWithValue("$at", DateTimeOffset.UtcNow.ToString("O"));
        await cmd.ExecuteNonQueryAsync(ct);
    }

    public async Task<IReadOnlyList<(int Sequence, string Stream, string Content)>> GetLogsAsync(long executionId, int tailBytes, string? stream = null, CancellationToken ct = default)
    {
        await using var c = new SqliteConnection(_connectionString);
        await c.OpenAsync(ct);
        await using var cmd = c.CreateCommand();
        cmd.CommandText = stream is null
            ? "SELECT sequence, stream, content FROM execution_logs WHERE execution_id=$eid ORDER BY sequence DESC"
            : "SELECT sequence, stream, content FROM execution_logs WHERE execution_id=$eid AND stream=$stream ORDER BY sequence DESC";
        cmd.Parameters.AddWithValue("$eid", executionId);
        if (stream is not null) cmd.Parameters.AddWithValue("$stream", stream);
        var rows = new List<(int, string, string)>();
        await using var r = await cmd.ExecuteReaderAsync(ct);
        while (await r.ReadAsync(ct)) rows.Add((r.GetInt32(0), r.GetString(1), r.GetString(2)));
        rows.Reverse();
        var joined = string.Join("", rows.Select(x => x.Item3));
        if (joined.Length <= tailBytes) return rows;
        var trimmed = joined[^tailBytes..];
        return new List<(int, string, string)> { (0, "tail", trimmed) };
    }

    // -------------------------------------------------------------------------
    // Helpers
    // -------------------------------------------------------------------------

    private static ExecutionRecord Read(SqliteDataReader r) => new(
        r.GetInt64(0), r.GetInt64(1), r.GetString(2), r.GetInt32(3), r.GetString(4),
        r.GetString(5), r.GetString(6), r.GetString(7),
        DateTimeOffset.Parse(r.GetString(8)),
        r.IsDBNull(9) ? null : DateTimeOffset.Parse(r.GetString(9)),
        r.IsDBNull(10) ? null : r.GetInt32(10),
        r.GetString(11),
        r.IsDBNull(12) ? null : r.GetString(12),
        r.IsDBNull(13) ? null : r.GetString(13),
        r.IsDBNull(14) ? null : r.GetString(14),
        r.GetString(15));
}
