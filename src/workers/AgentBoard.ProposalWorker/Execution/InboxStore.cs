using System.Text.Json;
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
///
/// 2026-09-02 P0-3 round-11 review: the UNIQUE index on the inbox is a
/// PARTIAL index — only rows in non-terminal states (pending / dispatching
/// / dispatched) participate. Terminal rows (completed / failed / cancelled)
/// are NOT in the index, so a re-dispatched message with the same
/// execution_key after a previous attempt finished is allowed to enqueue a
/// fresh attempt row instead of being silently dropped as a duplicate.
/// Without the partial index, a one-shot failure (e.g. adapter throws
/// before the work item could advance) would permanently block the
/// execution_key from ever being re-dispatched, leaving the upstream
/// proposal stuck.
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
            """;
        cmd.ExecuteNonQuery();
        // P0-3 (2026-09-02): idempotency must allow re-dispatch after a
        // terminal row exists. The old full-column UNIQUE index permanently
        // blocked retry of any execution_key that had ever reached a
        // terminal state, so a single failed attempt (e.g. adapter crash
        // before the proposal could be advanced) stranded the proposal
        // forever. Migrate by dropping the legacy index if it exists and
        // creating a partial index that only constrains non-terminal rows.
        EnsurePartialUniqueIndex(c);
    }

    /// <summary>
    /// Drop the legacy full-column UNIQUE index (if present) and create
    /// the partial UNIQUE index that only covers non-terminal rows.
    /// Idempotent — safe to call on every startup.
    /// </summary>
    private void EnsurePartialUniqueIndex(SqliteConnection c)
    {
        using var drop = c.CreateCommand();
        drop.CommandText = "DROP INDEX IF EXISTS ux_inbox_execution_key";
        drop.ExecuteNonQuery();

        using var partial = c.CreateCommand();
        partial.CommandText = """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_inbox_active_execution_key
              ON worker_execution_inbox(execution_key)
              WHERE status NOT IN ('completed', 'failed', 'cancelled')
            """;
        partial.ExecuteNonQuery();

        using var status = c.CreateCommand();
        status.CommandText = "CREATE INDEX IF NOT EXISTS ix_inbox_status ON worker_execution_inbox(status)";
        status.ExecuteNonQuery();
    }

    /// <summary>
    /// Try to enqueue. Returns the row id (existing or new) and a flag
    /// indicating whether the row was newly inserted.
    ///
    /// SQLite-specific note: <c>last_insert_rowid()</c> does NOT change when
    /// <c>INSERT OR IGNORE</c> is ignored due to a UNIQUE constraint on a
    /// non-rowid column. The dedupe index is now a partial UNIQUE that
    /// only covers non-terminal rows, so a re-dispatch after a terminal
    /// (completed / failed / cancelled) row always inserts a fresh row.
    /// We still distinguish new vs. existing via <c>ExecuteNonQuery</c>'s
    /// rowcount, then look up the id separately. P0-3 (2026-09-02): we
    /// must pick the most recent row, not the oldest — after a
    /// completed attempt is in the table alongside the new pending
    /// attempt, <c>SELECT id WHERE execution_key=?</c> without ORDER BY
    /// is free to return the stale completed id and confuse the caller
    /// (the dispatcher would race the legacy row).
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

        // Pick the most recent row by id (latest attempt wins), not the
        // earliest — see P0-3 note above.
        await using var sel = c.CreateCommand();
        sel.CommandText = "SELECT id FROM worker_execution_inbox WHERE execution_key=$key ORDER BY id DESC LIMIT 1";
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

    /// <summary>
    /// Coarse classification of a <see cref="Microsoft.Data.Sqlite.SqliteException"/>.
    /// 2026-08-29 review follow-up (round 10): every DB seam
    /// (SELECT pending rows, UPDATE claim, terminal persistence
    /// write, enqueue INSERT) must share the same error
    /// classification. The previous design only classified the
    /// claim and terminal-write paths; a SELECT-side
    /// <c>SqliteException</c> (e.g. schema drift, CORRUPT,
    /// NOTADB, disk I/O) silently fell through to a generic
    /// "log + retry on next wake" path that would loop forever
    /// without flipping the worker to degraded. The same is
    /// true for the enqueue path. Round-10 fix: every seam uses
    /// this classifier and every caller checks the result.
    /// </summary>
    public enum SqliteErrorKind
    {
        /// <summary>SQLITE_BUSY (5) or SQLITE_LOCKED (6) — retry with short backoff.</summary>
        Transient,
        /// <summary>Anything else (NOTADB, CORRUPT, schema mismatch, disk I/O, etc.) — Worker must be marked degraded and operator must intervene.</summary>
        Permanent,
        /// <summary>The exception is not a <see cref="Microsoft.Data.Sqlite.SqliteException"/>; treat as Permanent (defensive).</summary>
        Unknown,
    }

    /// <summary>
    /// Classify a <see cref="Microsoft.Data.Sqlite.SqliteException"/> for
    /// the dispatcher's degraded-vs-retry decision. See
    /// <see cref="SqliteErrorKind"/> for the contract.
    /// </summary>
    public static SqliteErrorKind ClassifySqliteException(Exception ex) =>
        ex is Microsoft.Data.Sqlite.SqliteException sql
            ? IsTransientSqliteLockError(sql) ? SqliteErrorKind.Transient : SqliteErrorKind.Permanent
            : SqliteErrorKind.Unknown;

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
    /// 2026-08-29 round-10 follow-up: the round-9 design
    /// checked capacity BEFORE duplicate, which produced a
    /// different but related invariant violation. With inbox at
    /// capacity and a legitimate Rabbit redelivery (e.g. ACK
    /// lost in flight), the consumer would see capacity-exceeded
    /// and NACK-requeue a duplicate — the broker would re-deliver
    /// the same message indefinitely AND, on the direct queue,
    /// the high-watermark path would also cancel the direct
    /// consumer (which then could not resume, see round-10
    /// #1). Fix: check duplicate FIRST inside the transaction.
    /// Already-existing work MUST be treated as Duplicate
    /// (idempotency) and never as CapacityExceeded.
    ///
    /// Order inside the transaction:
    ///   1. SELECT id WHERE execution_key=? — duplicate check
    ///   2. If hit, return Duplicate (no capacity consumed)
    ///   3. COUNT(*) pending
    ///   4. If at/above limit, return CapacityExceeded (no insert)
    ///   5. INSERT
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
        // of the transaction so the duplicate / count / insert
        // sequence is atomic w.r.t. other writers. The consumer
        // thread is the only writer in practice, but a future
        // drain that also touches the inbox would otherwise see
        // torn state. Use a transaction even when limit <= 0 so
        // the path is uniform; SQLite BEGIN is cheap on a
        // single-writer DB.
        await using var tx = (SqliteTransaction)await c.BeginTransactionAsync(ct);
        try
        {
            // 1. Duplicate check FIRST. If the execution_key is
            //    already in the inbox (regardless of status), it
            //    is a Rabbit redelivery and MUST be treated as a
            //    Duplicate — never CapacityExceeded. A redelivery
            //    cannot consume admission-control capacity because
            //    the work is already admitted; doing so would
            //    NACK the broker and create an infinite redelivery
            //    loop (and, on the direct queue, cancel the only
            //    consumer we have). Round-10 follow-up.
            long existingId = 0;
            await using (var sel = c.CreateCommand())
            {
                sel.Transaction = tx;
                sel.CommandText = "SELECT id FROM worker_execution_inbox WHERE execution_key=$key";
                sel.Parameters.AddWithValue("$key", request.ExecutionKey);
                var raw = await sel.ExecuteScalarAsync(ct);
                if (raw is not null && raw is not DBNull)
                {
                    existingId = Convert.ToInt64(raw);
                }
            }
            if (existingId != 0)
            {
                // Idempotency: a redelivery / duplicate. ACK on
                // the broker side and do NOT touch capacity.
                // Round-10 invariant: a work item already in
                // the system cannot be re-counted against the
                // high-watermark.
                await tx.CommitAsync(ct);
                return (EnqueueWithinCapacityOutcome.Duplicate, existingId);
            }

            // 2. Capacity check. Only reached when the request is
            //    genuinely a NEW work item.
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

            // 3. Insert. We already verified the execution_key is
            //    absent (step 1) and capacity is OK (step 2), so
            //    a plain INSERT (not INSERT OR IGNORE) is correct
            //    here — the UNIQUE index is a safety net, not the
            //    primary contract. A UNIQUE violation at this
            //    point would mean a concurrent writer slipped in
            //    between BEGIN IMMEDIATE and the INSERT, which
            //    should be impossible (BEGIN IMMEDIATE holds
            //    RESERVED) but we let SQLite's UNIQUE catch it
            //    just in case.
            long inboxId;
            await using (var ins = c.CreateCommand())
            {
                ins.Transaction = tx;
                ins.CommandText = """
                    INSERT INTO worker_execution_inbox
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
                await ins.ExecuteNonQueryAsync(ct);
            }

            await using (var sel2 = c.CreateCommand())
            {
                sel2.Transaction = tx;
                sel2.CommandText = "SELECT id FROM worker_execution_inbox WHERE execution_key=$key";
                sel2.Parameters.AddWithValue("$key", request.ExecutionKey);
                inboxId = Convert.ToInt64(await sel2.ExecuteScalarAsync(ct) ?? 0L);
            }

            await tx.CommitAsync(ct);
            return (EnqueueWithinCapacityOutcome.Enqueued, inboxId);
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
                Source: "db-refill",
                // P0-2：inbox 表没有 task_type 列；从 payload_json 恢复
                TaskType: TryGetTaskType(r.GetString(6)));
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
                Source: "startup-recovery",
                // P0-2：inbox 表没有 task_type 列；从 payload_json 恢复
                TaskType: TryGetTaskType(r.GetString(6)));
            rows.Add(new InFlightExecution(req, r.GetInt64(0)));
        }
        return rows;
    }

    /// <summary>
    /// P0-2（2026-09-01 review）：inbox 表没有 task_type 列，但 payload_json
    /// 里序列化了完整 WorkflowMessage（含 task_type）。refill 路径从 payload
    /// 恢复它；解析失败（legacy payload / 非 workflow 行）返回 null，prompt
    /// 退回 implementation 语义。
    /// </summary>
    private static string? TryGetTaskType(string payloadJson)
    {
        try
        {
            using var doc = JsonDocument.Parse(payloadJson);
            return doc.RootElement.TryGetProperty("task_type", out var t) &&
                   t.ValueKind == JsonValueKind.String
                ? t.GetString()
                : null;
        }
        catch (JsonException)
        {
            return null;
        }
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
