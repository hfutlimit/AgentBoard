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

    // Test-only instrumentation counter for the dispatcher's idle
    // wake-cycle. Tests assert this stays bounded (e.g. ~ timer
    // cadence) when no work is pending; without coalescing the
    // WakeSignal the counter would grow into the thousands per
    // second.
    private long _getOldestPendingFlightsCalls;
    public long GetOldestPendingFlightsCalls => Interlocked.Read(ref _getOldestPendingFlightsCalls);

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
        /// <summary>SQLite raised a transient lock-contention error (BUSY / LOCKED). Row stays `pending`; retry on the next cycle.</summary>
        TransientFailure,
        /// <summary>SQLite raised a non-transient error (CORRUPT, NOTADB, schema mismatch, etc.). Worker is in a degraded state and must stop scheduling; the row stays `pending` so the operator can intervene.</summary>
        PermanentFailure,
    }

    /// <summary>
    /// True iff <paramref name="ex"/>'s error code is one we
    /// know to recover from a single retry / short backoff. The
    /// 2026-08-29 review call-out: previously the catch was broad
    /// (any <see cref="Microsoft.Data.Sqlite.SqliteException"/>),
    /// which meant schema drift, corruption, or a missing table
    /// would enter the 2-second hot-retry loop forever and create
    /// a log-storm. Only SQLITE_BUSY (5) and SQLITE_LOCKED (6) are
    /// recoverable in practice; everything else is permanent.
    /// </summary>
    internal static bool IsTransientSqliteLockError(Microsoft.Data.Sqlite.SqliteException ex) =>
        ex.SqliteErrorCode == 5 /* SQLITE_BUSY */ || ex.SqliteErrorCode == 6 /* SQLITE_LOCKED */;

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
        catch (Microsoft.Data.Sqlite.SqliteException ex) when (IsTransientSqliteLockError(ex))
        {
            // SQLITE_BUSY / SQLITE_LOCKED — row stays `pending`; the
            // dispatcher's next refill cycle re-attempts. Without
            // this branch the dispatcher's outer catch would call
            // MarkFailedAsync and silently move the row to
            // `completed` without ever executing it.
            _log.LogWarning(ex,
                "Inbox.TryClaimAsync transient lock failure for inbox {InboxId} ({ErrorCode}); row stays pending and will be retried",
                inboxId, ex.SqliteErrorCode);
            return TryClaimOutcome.TransientFailure;
        }
        catch (Microsoft.Data.Sqlite.SqliteException ex)
        {
            // Anything else from SQLite is a real problem: corrupted
            // file, missing table, schema drift. Retrying would just
            // produce the same error every 2 seconds and burn the
            // log. The dispatcher sees PermanentFailure and stops
            // scheduling new work until an operator intervenes.
            _log.LogError(ex,
                "Inbox.TryClaimAsync non-transient DB failure for inbox {InboxId} ({ErrorCode}); worker degraded",
                inboxId, ex.SqliteErrorCode);
            return TryClaimOutcome.PermanentFailure;
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
    /// Cheap pending-row count. The consumer calls this on
    /// every successful enqueue to enforce the
    /// <c>Worker.MaxPendingInbox</c> high-watermark; it must
    /// not hydrate the row payloads. Uses an index on
    /// <c>status</c> (created in <see cref="Initialize"/>).
    /// 2026-08-29 review round-8 follow-up.
    /// </summary>
    public async Task<int> CountPendingAsync(CancellationToken ct)
    {
        await using var c = new SqliteConnection(_connectionString);
        await c.OpenAsync(ct);
        await using var cmd = c.CreateCommand();
        cmd.CommandText = "SELECT COUNT(*) FROM worker_execution_inbox WHERE status='pending'";
        var n = await cmd.ExecuteScalarAsync(ct);
        return Convert.ToInt32(n);
    }

    /// <summary>
    /// Outcome of <see cref="TryEnqueueWithinCapacityAsync"/>.
    /// 2026-08-29 round-9 review follow-up: capacity-exceeded
    /// MUST not produce a dedupe row (otherwise the next
    /// Rabbit redelivery would see the existing execution_key
    /// and ACK-drop, permanently losing the task).
    /// </summary>
    public enum EnqueueWithinCapacityOutcome
    {
        /// <summary>A new pending row was inserted; the consumer should ACK the Rabbit delivery.</summary>
        Enqueued,
        /// <summary>execution_key already exists in the inbox (Rabbit redelivery); ACK the duplicate.</summary>
        Duplicate,
        /// <summary>Pending count is at or above the high-watermark. NO row was inserted. The consumer MUST NACK-requeue so a peer (or this worker after the inbox drains) can take the message.</summary>
        CapacityExceeded,
    }

    /// <summary>
    /// 2026-08-29 round-9 review follow-up: the round-8 design
    /// had a fatal invariant violation — it would insert the
    /// inbox row FIRST and only THEN check the high-watermark,
    /// calling MarkFailedAsync to convert it to "completed" on
    /// overflow. That left a terminal dedupe record on disk,
    /// so when the NACK-requeued Rabbit message came back to
    /// the same worker, <c>INSERT OR IGNORE</c> matched the
    /// existing execution_key, returned <c>IsNew=false</c>, and
    /// the consumer ACK-dropped the redelivery. The task was
    /// silently lost. For the direct queue (only this worker
    /// consumes) the loss is deterministic.
    ///
    /// Fix: count + insert inside ONE transaction, and on
    /// capacity exceeded ROLL BACK the insert so no row is
    /// produced. The consumer then NACKs the Rabbit message
    /// back to the broker, which is the only place it can sit
    /// safely until the inbox drains.
    ///
    /// <c>limit</c> = 0 disables the high-watermark (unbounded
    /// enqueue, NOT recommended). Negative limits are treated
    /// as 0.
    /// </summary>
    public async Task<(EnqueueWithinCapacityOutcome Outcome, long InboxId)> TryEnqueueWithinCapacityAsync(
        ExecutionRequest request, int limit, CancellationToken ct)
    {
        await using var c = new SqliteConnection(_connectionString);
        await c.OpenAsync(ct);

        // BEGIN IMMEDIATE acquires a RESERVED lock at the start
        // of the transaction so the count-then-insert pair is
        // atomic w.r.t. other writers (the consumer thread is
        // the only writer in practice, but a future drain that
        // also touches the inbox would otherwise see torn state).
        // Use a transaction even when limit <= 0 so the path is
        // uniform; SQLite BEGIN is cheap on a single-writer DB.
        await using var tx = (SqliteTransaction)await c.BeginTransactionAsync(ct);
        try
        {
            if (limit > 0)
            {
                await using var count = c.CreateCommand();
                count.Transaction = tx;
                count.CommandText = "SELECT COUNT(*) FROM worker_execution_inbox WHERE status='pending'";
                var n = Convert.ToInt32(await count.ExecuteScalarAsync(ct));
                if (n >= limit)
                {
                    // Critical: NO INSERT happened. Roll back
                    // (technically a no-op since nothing was
                    // written, but explicit is clearer). The
                    // consumer will NACK the Rabbit message and
                    // it will be redelivered to a peer (or to
                    // us after the dispatcher catches up).
                    await tx.RollbackAsync(ct);
                    return (EnqueueWithinCapacityOutcome.CapacityExceeded, 0L);
                }
            }

            long inboxId;
            bool isNew;
            await using (var ins = c.CreateCommand())
            {
                ins.Transaction = tx;
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

            await using (var sel = c.CreateCommand())
            {
                sel.Transaction = tx;
                sel.CommandText = "SELECT id FROM worker_execution_inbox WHERE execution_key=$key";
                sel.Parameters.AddWithValue("$key", request.ExecutionKey);
                inboxId = (long)(await sel.ExecuteScalarAsync(ct) ?? 0L);
            }

            await tx.CommitAsync(ct);
            return (isNew ? EnqueueWithinCapacityOutcome.Enqueued : EnqueueWithinCapacityOutcome.Duplicate, inboxId);
        }
        catch
        {
            try { await tx.RollbackAsync(ct); } catch { /* best-effort */ }
            throw;
        }
    }

    /// <summary>
    /// Pull the oldest <paramref name="limit"/> pending inbox rows.
    /// Used by the Dispatcher's DB-first main loop: every wake
    /// (RabbitMQ signal, periodic timer, startup) the dispatcher
    /// queries the DB directly for the next row, claims it, runs
    /// it. The bounded channel no longer carries the work payload
    /// — it carries only a <see cref="WakeSignal"/> sentinel.
    ///
    /// 2026-08-29 review follow-up (round 7): added the
    /// <c>LIMIT @limit</c> cap. The previous version had no LIMIT,
    /// so a 50,000-row backlog would be SELECTed into a
    /// <c>List&lt;InFlightExecution&gt;</c> in one go. The
    /// payload_json column can be large (full agent context);
    /// loading 50,000 rows × ~10KB each = ~500 MB into a single
    /// list before the dispatcher even starts processing. The
    /// dispatcher should pull a small batch at a time, process
    /// them, then pull the next batch. The caller (dispatcher)
    /// controls the batch size; the default here is 100 which is
    /// enough to keep the channel wake → DB query → execute path
    /// tight without ballooning memory.
    /// </summary>
    public async Task<IReadOnlyList<InFlightExecution>> GetOldestPendingFlightsAsync(int limit, CancellationToken ct)
    {
        if (limit <= 0) return Array.Empty<InFlightExecution>();
        // Test instrumentation: count inbox-query invocations so
        // dispatcher hot-loop tests can assert the wake-signal
        // consumption path actually coalesces. The counter is
        // Interlocked so a future test that drives multiple
        // dispatchers concurrently does not lose increments. In
        // production this is a single increment per query; the cost
        // is negligible.
        Interlocked.Increment(ref _getOldestPendingFlightsCalls);
        var rows = new List<InFlightExecution>(Math.Min(limit, 64));
        await using var c = new SqliteConnection(_connectionString);
        await c.OpenAsync(ct);
        await using var cmd = c.CreateCommand();
        cmd.CommandText = """
            SELECT id, execution_key, workload_type, workload_id, agent_type, round, payload_json
            FROM worker_execution_inbox
            WHERE status='pending'
            ORDER BY id ASC
            LIMIT @limit
            """;
        cmd.Parameters.AddWithValue("@limit", limit);
        await using var r = await cmd.ExecuteReaderAsync(ct);
        while (await r.ReadAsync(ct))
        {
            var req = new ExecutionRequest(
                ExecutionKey: r.GetString(1),
                WorkloadType: r.GetString(2),
                WorkloadId: r.GetInt64(3),
                AgentType: r.GetString(4),
                Round: r.GetInt32(5),
                PayloadJson: r.GetString(6),
                Source: "db-refill");
            rows.Add(new InFlightExecution(req, r.GetInt64(0)));
        }
        return rows;
    }

    /// <summary>
    /// Kept for backward compatibility with tests that just want
    /// a "are there any pending rows" count. The 2026-08-29 review
    /// follow-up replaces production callers with
    /// <see cref="GetOldestPendingFlightsAsync(int, CancellationToken)"/>
    /// which is bounded and ordered; this method is unbounded and
    /// exists only so the test suite can count `pending` rows
    /// without rewriting the assertions.
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
