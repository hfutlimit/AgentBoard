namespace AgentBoard.ProposalWorker.Execution;

/// <summary>
/// DB-first dispatcher. Wakes on a <see cref="WakeSignal"/> from the
/// bounded <see cref="ExecutionChannel"/> (a RabbitMQ consumer
/// wrote one) OR on a periodic timer (in case signals were dropped
/// — the timer is a safety net), then queries the durable DB inbox
/// directly for the oldest pending row, claims it, and runs it.
///
/// 2026-08-29 review (round 7) follow-up: the previous design carried
/// the full <see cref="ExecutionRequest"/> in the channel and
/// used <c>TryRefillFromDbAsync</c> to push DB rows into the
/// channel. Under sustained RabbitMQ load, the channel could stay
/// permanently non-empty (live traffic), the refill short-circuit
/// was triggered, and DB-only pending rows (Pause-revert, startup
/// recovery tail) starved indefinitely. Switching the channel to a
/// wake-signal sentinel and reading the work directly from the DB
/// closes this — the DB is the durable queue, the channel is just
/// a "there's new work" notification.
///
/// The bounded channel still serves its real purpose: RabbitMQ
/// backpressure. If the Dispatcher stops reading wake signals
/// (paused / degraded), the consumer's WriteAsync blocks, which
/// blocks the BasicAck, which applies AMQP-level backpressure to
/// the broker.
/// </summary>
public sealed class ExecutionDispatcher : BackgroundService
{
    private readonly ExecutionChannel _channel;
    private readonly InboxStore _inbox;
    private readonly ExecutionCoordinator _coordinator;
    private readonly ILogger<ExecutionDispatcher> _log;

    /// <summary>
    /// Periodic wakeup. Wake signals from the bounded channel
    /// (RabbitMQ activity, or any other writer) drive the
    /// dispatcher's normal-case work; this timer is a safety
    /// net in case signals were dropped (e.g. consumer
    /// reconnect race, paused-then-resumed). Without the timer,
    /// a transient DB error during the previous drain would
    /// leave the dispatcher sleeping forever on an empty
    /// channel — the bounded channel can't fire a wake without a
    /// writer.
    /// </summary>
    private static readonly TimeSpan IdleWakeInterval = TimeSpan.FromSeconds(2);

    /// <summary>
    /// The dispatcher's main loop is bounded so a single wake
    /// cycle can't monopolise the worker under sustained load.
    /// 50 flights / 5 s keeps the channel responsive while
    /// ensuring the wake loop re-polls the DB at least every
    /// 5 s under load.
    /// </summary>
    private const int MaxFlightsPerWakeBatch = 50;
    private static readonly TimeSpan MaxWakeBatchDuration = TimeSpan.FromSeconds(5);

    /// <summary>
    /// How many rows to pull from the DB per internal query.
    /// Small enough to keep memory bounded; large enough to
    /// amortise the per-query latency over several rows. The
    /// dispatcher may call GetOldestPendingFlightsAsync multiple
    /// times per wake cycle (the outer loop pulls again if more
    /// rows are still pending).
    /// </summary>
    private const int DbQueryBatchSize = 16;

    public ExecutionDispatcher(
        ExecutionChannel channel,
        InboxStore inbox,
        ExecutionCoordinator coordinator,
        ILogger<ExecutionDispatcher> log)
    {
        _channel = channel;
        _inbox = inbox;
        _coordinator = coordinator;
        _log = log;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        _log.LogInformation("ExecutionDispatcher started (DB-first)");

        // Initial drain. A previous worker session may have left
        // the inbox with rows the new session hasn't seen yet
        // (e.g. crash between TryEnqueueAsync and channel wake,
        // or rows still in 'pending' from startup recovery).
        // The channel has no buffered wakes at this point (we're
        // the first reader); the timer hasn't fired yet. Run one
        // initial drain so the worker doesn't sit idle for up
        // to IdleWakeInterval on startup.
        await DrainFromDbAsync("startup", stoppingToken);

        try
        {
            while (!stoppingToken.IsCancellationRequested)
            {
                if (_coordinator.IsPaused())
                {
                    await Task.Delay(TimeSpan.FromMilliseconds(200), stoppingToken);
                    continue;
                }
                if (_coordinator.IsDegraded())
                {
                    _log.LogError(
                        "Worker is degraded ({Reason}); stopping dispatch. Operator must investigate and restart.",
                        _coordinator.DegradedReason ?? "(no reason set)");
                    break;
                }

                // Race a wake-signal against a periodic timer. The
                // bounded channel provides backpressure (producer
                // blocks if we're not reading), the timer is the
                // safety net. When the timer wins we cancel the
                // waiter's CTS so the pending ChannelReader
                // registration is released — no leak of pending
                // waiters across idle ticks. 2026-08-29 review #1
                // (round 6) and #1 (round 7).
                using (var waitCts = CancellationTokenSource.CreateLinkedTokenSource(stoppingToken))
                {
                    var wakeTimer = Task.Delay(IdleWakeInterval, stoppingToken);
                    var waitTask = _channel.Reader.WaitToReadAsync(waitCts.Token).AsTask();
                    var winner = await Task.WhenAny(waitTask, wakeTimer);
                    if (winner == waitTask)
                    {
                        // Wake-signal available; cancel the timer.
                        if (!await waitTask) break;  // channel closed
                    }
                    else
                    {
                        // Periodic wakeup. Cancel the wait so the
                        // pending ChannelReader registration is
                        // released.
                        waitCts.Cancel();
                        try { await waitTask; } catch (OperationCanceledException) { }
                    }
                }

                // Drain from the durable DB. The channel is just a
                // wake-up signal; the actual work payload lives
                // in the inbox.
                await DrainFromDbAsync("wake", stoppingToken);
            }
        }
        catch (OperationCanceledException) { /* shutdown */ }
        _log.LogInformation("ExecutionDispatcher stopped");
    }

    /// <summary>
    /// Process rows from the durable DB inbox until either the
    /// inbox is empty or the per-wake batch cap (50 flights / 5 s)
    /// is hit. Each iteration:
    ///   1. pulls up to <see cref="DbQueryBatchSize"/> oldest
    ///      pending rows from the DB (ORDER BY id ASC LIMIT N);
    ///   2. CAS-claims each (pending → dispatching) via
    ///      <see cref="InboxStore.TryClaimAsync"/>;
    ///   3. runs the coordinator with the (request, inboxId)
    ///      pair;
    ///   4. tracks the per-wake budget (count + wall clock).
    /// </summary>
    private async Task DrainFromDbAsync(string reason, CancellationToken stoppingToken)
    {
        var batchStart = DateTimeOffset.UtcNow;
        int flightsThisBatch = 0;

        while (!stoppingToken.IsCancellationRequested
               && !_coordinator.IsPaused()
               && !_coordinator.IsDegraded()
               && flightsThisBatch < MaxFlightsPerWakeBatch
               && (DateTimeOffset.UtcNow - batchStart) < MaxWakeBatchDuration)
        {
            IReadOnlyList<InFlightExecution> pending;
            try
            {
                pending = await _inbox.GetOldestPendingFlightsAsync(DbQueryBatchSize, stoppingToken);
            }
            catch (Exception ex)
            {
                _log.LogError(ex, "DB pending list failed during {Reason} drain; will retry on next wake", reason);
                return;
            }
            if (pending.Count == 0) return;

            foreach (var flight in pending)
            {
                if (flightsThisBatch >= MaxFlightsPerWakeBatch) break;
                if (stoppingToken.IsCancellationRequested) return;
                if (_coordinator.IsPaused() || _coordinator.IsDegraded()) return;

                flightsThisBatch++;
                try
                {
                    // Tri-state CAS. Transient DB failure means the
                    // row is still `pending` and will be retried on
                    // the next wake cycle. Permanent DB failure
                    // marks the worker degraded and stops
                    // dispatching — see 2026-08-29 review #6
                    // (round 6).
                    var claim = await _inbox.TryClaimAsync(flight.InboxId, stoppingToken);
                    if (claim == InboxStore.TryClaimOutcome.AlreadyClaimed)
                    {
                        _log.LogDebug("Inbox {InboxId} already claimed; skipping", flight.InboxId);
                        continue;
                    }
                    if (claim == InboxStore.TryClaimOutcome.TransientFailure)
                    {
                        _log.LogWarning(
                            "Inbox {InboxId} claim hit transient DB failure; row stays pending and will be retried on the next wake",
                            flight.InboxId);
                        continue;
                    }
                    if (claim == InboxStore.TryClaimOutcome.PermanentFailure)
                    {
                        _coordinator.MarkDegraded(
                            $"Inbox.TryClaimAsync non-transient DB failure on inbox {flight.InboxId}; stop dispatching");
                        _log.LogError(
                            "Inbox {InboxId} claim hit permanent DB failure; worker degraded; dispatch loop will exit",
                            flight.InboxId);
                        return;
                    }
                    await _coordinator.ExecuteAsync(flight.Request, flight.InboxId, stoppingToken);
                }
                catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
                {
                    return;
                }
                catch (Exception ex)
                {
                    // Claim succeeded, agent crashed. MarkFailedAsync
                    // is the right call here. If it also fails
                    // transiently, the next wake cycle / operator
                    // pass will reconcile.
                    _log.LogError(ex, "Execution {Key} crashed in dispatcher", flight.Request.ExecutionKey);
                    try { await _inbox.MarkFailedAsync(flight.InboxId, ex.Message, CancellationToken.None); }
                    catch { /* swallow */ }
                }
            }
        }
    }
}
