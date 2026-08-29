using Microsoft.Data.Sqlite;
using Microsoft.Extensions.Options;

namespace AgentBoard.ProposalWorker.Execution;

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
    /// Try to enqueue. Returns the row id (existing or new) and a flag
    /// indicating whether the row was newly inserted.
    ///
    /// SQLite-specific note: <c>last_insert_rowid()</c> does NOT change when
    /// <c>INSERT OR IGNORE</c> is ignored due to a UNIQUE constraint on a
    /// non-rowid column (which is our case: <c>UNIQUE(execution_key)</c>).
    /// So we MUST distinguish new vs. existing via <c>ExecuteNonQuery</c>'s
    /// rowcount, then look up the id separately.
    /// </summary>
    public async Task<(long InboxId, bool IsNew)> TryEnqueueAsync(ExecutionRequest request, CancellationToken ct)
    {
        await using var c = new SqliteConnection(_connectionString);
        await c.OpenAsync(ct);

        bool isNew;
        await using (var ins = c.CreateCommand())
        {
            ins.CommandText = """
                INSERT OR IGNORE INTO worker_execution_inbox
                  (execution_key, workload_type, workload_id, agent_type, round, payload_json, status, received_at)
                VALUES($key,$wtype,$wid,$agent,$round,$payload,'pending',$at)
                """;
            ins.Parameters.AddWithValue("$key", request.ExecutionKey);
            ins.Parameters.AddWithValue("$wtype", request.WorkloadType);
            ins.Parameters.AddWithValue("$wid", request.WorkloadId);
            ins.Parameters.AddWithValue("$agent", request.AgentType);
            ins.Parameters.AddWithValue("$round", request.Round);
            ins.Parameters.AddWithValue("$payload", request.PayloadJson);
            ins.Parameters.AddWithValue("$at", DateTimeOffset.UtcNow.ToString("O"));
            var n = await ins.ExecuteNonQueryAsync(ct);
            isNew = (n == 1);
        }

        await using var sel = c.CreateCommand();
        sel.CommandText = "SELECT id FROM worker_execution_inbox WHERE execution_key=$key";
        sel.Parameters.AddWithValue("$key", request.ExecutionKey);
        var id = (long)(await sel.ExecuteScalarAsync(ct) ?? 0L);
        return (id, isNew);
    }

    /// <summary>
    /// CAS: pending → dispatching. Returns the outcome so the caller can
    /// distinguish a real transient DB failure (the row stays `pending`
    /// and should be retried) from a successful no-op (the row was
    /// already claimed by another consumer). Fix for the 2026-08-29
    /// review: a transient SQLite exception previously bubbled up to the
    /// dispatcher's generic catch, which called MarkFailedAsync and
    /// silently moved the row to `completed` without ever executing it.
    /// </summary>
    public enum TryClaimOutcome
    {
        /// <summary>This caller won the CAS and the row is now `dispatching`.</summary>
        Claimed,
        /// <summary>Row is no longer `pending` (already claimed or completed). Skip.</summary>
        AlreadyClaimed,
        /// <summary>SQLite raised a transient exception (BUSY, I/O, etc.). Row stays `pending`; retry on the next cycle.</summary>
        TransientFailure,
    }

    public async Task<TryClaimOutcome> TryClaimAsync(long inboxId, CancellationToken ct)
    {
        try
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
            var n = await cmd.ExecuteNonQueryAsync(ct);
            return n == 1 ? TryClaimOutcome.Claimed : TryClaimOutcome.AlreadyClaimed;
        }
        catch (Microsoft.Data.Sqlite.SqliteException ex)
        {
            // SQLITE_BUSY (5), SQLITE_IOERR (10), SQLITE_FULL (13),
            // SQLITE_CANTOPEN (14), and friends. The row stays `pending`
            // so the dispatcher's next refill cycle re-attempts the
            // claim. Without this tri-state the dispatcher's outer catch
            // would call MarkFailedAsync and silently move the row to
            // `completed` without ever executing it.
            _log.LogWarning(ex,
                "Inbox.TryClaimAsync transient DB failure for inbox {InboxId} ({ErrorCode}); row stays pending and will be retried",
                inboxId, ex.SqliteErrorCode);
            return TryClaimOutcome.TransientFailure;
        }
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
    /// Atomic revert: <c>dispatching → pending</c>. Used by the Coordinator
    /// when it sees <see cref="AgentBoard.ProposalWorker.WorkerState.Paused"/>
    /// AFTER the Dispatcher has already moved the row to <c>dispatching</c>.
    /// Without this revert the row sits in <c>dispatching</c> forever (the
    /// Dispatcher won't re-claim a non-pending row, and no startup-recovery
    /// will fire until the worker restarts). Fix for #4 in the 2026-08-28 review.
    /// </summary>
    public async Task MarkPendingAsync(long inboxId, CancellationToken ct)
    {
        await using var c = new SqliteConnection(_connectionString);
        await c.OpenAsync(ct);
        await using var cmd = c.CreateCommand();
        cmd.CommandText = """
            UPDATE worker_execution_inbox
            SET status='pending', dispatched_at=NULL
            WHERE id=$id AND status='dispatching'
            """;
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

    /// <summary>
    /// List every inbox row that survived startup in <c>pending</c> state. The
    /// caller (Program.cs / ExecutionDispatcher) re-enqueues each into the
    /// in-memory channel so the work is not lost. Fix for #6 in the
    /// 2026-08-28 review: a process crash between <c>TryEnqueueAsync</c> and
    /// <c>Channel.Writer.WriteAsync</c> previously left a row stuck in
    /// <c>pending</c> forever (no consumer ever saw the message after restart
    /// because RabbitMQ redelivery was a no-op against the duplicate row).
    /// </summary>
    public async Task<IReadOnlyList<InFlightExecution>> ListPendingAsync(CancellationToken ct)
    {
        var rows = new List<InFlightExecution>();
        await using var c = new SqliteConnection(_connectionString);
        await c.OpenAsync(ct);
        await using var cmd = c.CreateCommand();
        cmd.CommandText = """
            SELECT id, execution_key, workload_type, workload_id, agent_type, round, payload_json
            FROM worker_execution_inbox
            WHERE status='pending'
            ORDER BY id ASC
            """;
        await using var r = await cmd.ExecuteReaderAsync(ct);
        while (await r.ReadAsync(ct))
        {
            // Reconstruct an ExecutionRequest so the dispatcher can re-enqueue
            // it. Round/Payload/agent match what the consumer originally wrote;
            // work_unit_id (the DB column) becomes WorkloadId on the request
            // (the in-memory DTO), so round-trip is identity-preserving.
            var req = new ExecutionRequest(
                ExecutionKey: r.GetString(1),
                WorkloadType: r.GetString(2),
                WorkloadId: r.GetInt64(3),
                AgentType: r.GetString(4),
                Round: r.GetInt32(5),
                PayloadJson: r.GetString(6),
                Source: "startup-recovery");
            rows.Add(new InFlightExecution(req, r.GetInt64(0)));
        }
        return rows;
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
