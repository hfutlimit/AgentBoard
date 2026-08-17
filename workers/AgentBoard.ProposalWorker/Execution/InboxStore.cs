using Microsoft.Data.Sqlite;
using Microsoft.Extensions.Options;

namespace AgentBoard.ProposalWorker;

/// <summary>
/// Sprint 2: durable inbox + idempotency. UNIQUE(execution_key) on the inbox
/// table is the single source of truth for "have I already seen this message".
///
/// State machine: <c>pending → dispatching → dispatched → completed</c>.
/// On startup, all rows stuck in <c>dispatching</c> (i.e. last shutdown
/// happened after dispatch but before the execution finished) are reset to
/// <c>pending</c> for re-dispatch.
/// </summary>
public sealed class InboxStore
{
    private readonly string _connectionString;
    private readonly ILogger<InboxStore> _log;

    public InboxStore(ExecutionStore store, ILogger<InboxStore> log)
    {
        _connectionString = store.ConnectionString;
        _log = log;
        Initialize();
    }

    private void Initialize()
    {
        using var c = new SqliteConnection(_connectionString);
        c.Open();
        using var cmd = c.CreateCommand();
        cmd.CommandText = """
            CREATE TABLE IF NOT EXISTS worker_execution_inbox (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              execution_key TEXT NOT NULL,
              workload_type TEXT NOT NULL,
              workload_id INTEGER NOT NULL,
              agent_type TEXT NOT NULL,
              round INTEGER NOT NULL DEFAULT 0,
              payload_json TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending',
              received_at TEXT NOT NULL,
              dispatched_at TEXT NULL,
              completed_at TEXT NULL,
              attempt INTEGER NOT NULL DEFAULT 1,
              error_message TEXT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS ux_inbox_execution_key ON worker_execution_inbox(execution_key);
            CREATE INDEX IF NOT EXISTS ix_inbox_status ON worker_execution_inbox(status);
            """;
        cmd.ExecuteNonQuery();
    }

    /// <summary>
    /// Try to enqueue. Returns the row id (existing or new) so the caller can
    /// decide whether to dispatch or ACK-and-drop. INSERT OR IGNORE is the
    /// atomic idempotency primitive — duplicate keys get rowid=-1, we then
    /// SELECT the existing row.
    /// </summary>
    public async Task<(long InboxId, bool IsNew)> TryEnqueueAsync(ExecutionRequest request, CancellationToken ct)
    {
        await using var c = new SqliteConnection(_connectionString);
        await c.OpenAsync(ct);

        long insertedId;
        await using (var ins = c.CreateCommand())
        {
            ins.CommandText = """
                INSERT OR IGNORE INTO worker_execution_inbox
                  (execution_key, workload_type, workload_id, agent_type, round, payload_json, status, received_at)
                VALUES($key,$wtype,$wid,$agent,$round,$payload,'pending',$at);
                SELECT last_insert_rowid();
                """;
            ins.Parameters.AddWithValue("$key", request.ExecutionKey);
            ins.Parameters.AddWithValue("$wtype", request.WorkloadType);
            ins.Parameters.AddWithValue("$wid", request.WorkloadId);
            ins.Parameters.AddWithValue("$agent", request.AgentType);
            ins.Parameters.AddWithValue("$round", request.Round);
            ins.Parameters.AddWithValue("$payload", request.PayloadJson);
            ins.Parameters.AddWithValue("$at", DateTimeOffset.UtcNow.ToString("O"));
            insertedId = (long)(await ins.ExecuteScalarAsync(ct) ?? 0L);
        }

        if (insertedId > 0) return (insertedId, true);

        // Duplicate: return the existing row id.
        await using var sel = c.CreateCommand();
        sel.CommandText = "SELECT id FROM worker_execution_inbox WHERE execution_key=$key";
        sel.Parameters.AddWithValue("$key", request.ExecutionKey);
        var existing = (long)(await sel.ExecuteScalarAsync(ct) ?? 0L);
        return (existing, false);
    }

    /// <summary>
    /// CAS: pending → dispatching. Returns true if this caller won the race.
    /// </summary>
    public async Task<bool> TryClaimAsync(long inboxId, CancellationToken ct)
    {
        await using var c = new SqliteConnection(_connectionString);
        await c.OpenAsync(ct);
        await using var cmd = c.CreateCommand();
        cmd.CommandText = """
            UPDATE worker_execution_inbox
            SET status='dispatching', dispatched_at=$at, attempt=attempt+1
            WHERE id=$id AND status='pending'
            """;
        cmd.Parameters.AddWithValue("$at", DateTimeOffset.UtcNow.ToString("O"));
        cmd.Parameters.AddWithValue("$id", inboxId);
        return await cmd.ExecuteNonQueryAsync(ct) == 1;
    }

    public async Task MarkCompletedAsync(long inboxId, CancellationToken ct)
    {
        await using var c = new SqliteConnection(_connectionString);
        await c.OpenAsync(ct);
        await using var cmd = c.CreateCommand();
        cmd.CommandText = "UPDATE worker_execution_inbox SET status='completed', completed_at=$at WHERE id=$id";
        cmd.Parameters.AddWithValue("$at", DateTimeOffset.UtcNow.ToString("O"));
        cmd.Parameters.AddWithValue("$id", inboxId);
        await cmd.ExecuteNonQueryAsync(ct);
    }

    public async Task MarkFailedAsync(long inboxId, string error, CancellationToken ct)
    {
        await using var c = new SqliteConnection(_connectionString);
        await c.OpenAsync(ct);
        await using var cmd = c.CreateCommand();
        cmd.CommandText = "UPDATE worker_execution_inbox SET status='completed', completed_at=$at, error_message=$err WHERE id=$id";
        cmd.Parameters.AddWithValue("$at", DateTimeOffset.UtcNow.ToString("O"));
        cmd.Parameters.AddWithValue("$err", error);
        cmd.Parameters.AddWithValue("$id", inboxId);
        await cmd.ExecuteNonQueryAsync(ct);
    }

    /// <summary>
    /// Startup recovery: any row stuck in <c>dispatching</c> from a previous
    /// crash gets reset to <c>pending</c> so it can be re-dispatched.
    /// </summary>
    public async Task<int> ResetStuckDispatchingAsync(CancellationToken ct)
    {
        await using var c = new SqliteConnection(_connectionString);
        await c.OpenAsync(ct);
        await using var cmd = c.CreateCommand();
        cmd.CommandText = "UPDATE worker_execution_inbox SET status='pending' WHERE status='dispatching'";
        var n = await cmd.ExecuteNonQueryAsync(ct);
        if (n > 0) _log.LogWarning("Reset {Count} inbox rows from dispatching → pending (startup recovery)", n);
        return n;
    }

    public async Task<InboxRecord?> GetAsync(long inboxId, CancellationToken ct = default)
    {
        await using var c = new SqliteConnection(_connectionString);
        await c.OpenAsync(ct);
        await using var cmd = c.CreateCommand();
        cmd.CommandText = "SELECT id,execution_key,workload_type,workload_id,agent_type,round,payload_json,status,received_at,dispatched_at,completed_at,attempt,error_message FROM worker_execution_inbox WHERE id=$id";
        cmd.Parameters.AddWithValue("$id", inboxId);
        await using var r = await cmd.ExecuteReaderAsync(ct);
        if (!await r.ReadAsync(ct)) return null;
        return new InboxRecord(
            r.GetInt64(0), r.GetString(1), r.GetString(2), r.GetInt64(3), r.GetString(4),
            r.GetInt32(5), r.GetString(6), r.GetString(7),
            DateTimeOffset.Parse(r.GetString(8)),
            r.IsDBNull(9) ? null : DateTimeOffset.Parse(r.GetString(9)),
            r.IsDBNull(10) ? null : DateTimeOffset.Parse(r.GetString(10)),
            r.GetInt32(11),
            r.IsDBNull(12) ? null : r.GetString(12));
    }
}
