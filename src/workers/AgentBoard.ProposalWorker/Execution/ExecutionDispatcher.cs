namespace AgentBoard.ProposalWorker.Execution;

/// <summary>
/// Sprint 3. Background task that drains the in-memory dispatch channel and
/// hands each request to <see cref="ExecutionCoordinator"/>. One exception in
/// one execution must NOT crash the channel loop — caught and logged inline.
/// </summary>
public sealed class ExecutionDispatcher : BackgroundService
{
    private readonly ExecutionChannel _channel;
    private readonly InboxStore _inbox;
    private readonly ExecutionCoordinator _coordinator;
    private readonly ILogger<ExecutionDispatcher> _log;

    public ExecutionDispatcher(ExecutionChannel channel, InboxStore inbox, ExecutionCoordinator coordinator, ILogger<ExecutionDispatcher> log)
    {
        _channel = channel;
        _inbox = inbox;
        _coordinator = coordinator;
        _log = log;
    }

    /// <summary>
    /// Periodic wakeup interval. The bounded channel is the
    /// wakeup/acceleration path; the DB inbox is the durable queue; the
    /// timer is the safety net. Without it, a transient DB error during
    /// the previous refill would leave the dispatcher sleeping forever
    /// on an empty channel — and any DB-only row (e.g. reverted from
    /// dispatching by a Pause race, or inserted directly by an operator
    /// for a re-run) would be lost until the next manual operator
    /// action. 2026-08-29 review fix.
    /// </summary>
    private static readonly TimeSpan IdleWakeInterval = TimeSpan.FromSeconds(2);

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        _log.LogInformation("ExecutionDispatcher started");

        // Startup recovery + ongoing DB-pending refill.
        // 2026-08-28 review (#2): a crash between TryEnqueueAsync and
        // Channel.Writer.WriteAsync previously left a row stuck in
        // `pending` forever. The startup scan drains the durable inbox
        // here, after the dispatcher is the live consumer.
        // 2026-08-29 review (#2): the previous version called
        // ListPendingAsync exactly ONCE at startup. A backlog larger
        // than channel capacity stranded the excess in DB pending
        // forever. We now also call TryRefillFromDbAsync on every idle
        // cycle (see the main loop below), so the channel acts as a
        // wakeup/acceleration mechanism and the DB inbox is the real
        // durable queue. With the Coordinator's Paused branch also
        // routing through DB pending (no channel re-enqueue — see
        // ExecutionCoordinator), the refill is the single path for any
        // row that ended up back in the inbox table.
        try
        {
            await TryRefillFromDbAsync(stoppingToken, "startup");
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "Startup recovery from pending inbox failed; continuing with empty channel");
        }

        try
        {
            // Poll-and-skip-when-paused instead of an unconditional
            // `await foreach (ReadAllAsync)`. The previous version drained
            // the channel even while paused, which races with the Coordinator
            // (which re-enqueues a flight when it sees Paused) and produced a
            // tight re-enqueue → re-claim → re-enqueue loop until Pause was
            // cleared. Now the Dispatcher holds any in-flight items in the
            // channel buffer while paused, and the Coordinator's
            // `dispatching → pending` revert (plus its re-enqueue) only
            // matters for the narrow race where Pause is set after the
            // Dispatcher has already taken a flight. Fix for #4 in the
            // 2026-08-28 review.
            //
            // #3 follow-up: the inner drain loop also rechecks `IsPaused()`.
            // Without the recheck, an operator can flip Pause → false → true
            // between the outer check and the inner drain, and the inner
            // loop will keep claiming rows the Coordinator then reverts
            // (dispatching → pending → re-enqueue → claim → revert …) in a
            // tight CPU / SQLite UPDATE loop. Each iteration also pays a
            // MarkPendingAsync + WriteAsync round-trip, so it is not free.
            //
            // 2026-08-29 follow-up: refill the channel from DB pending on
            // every idle cycle so a backlog larger than channel capacity
            // eventually drains. The refilled rows go through the same
            // drain path; the dispatcher is the only reader so the only
            // writer to the channel is the dispatcher's own refill loop
            // (and the RabbitMQ consumer, which is not on the critical
            // path here).
            //
            // 2026-08-29 follow-up: race WaitToReadAsync with a periodic
            // wakeup so a transient DB error during the previous refill
            // does not leave the dispatcher sleeping forever on an empty
            // channel. The bounded channel is the wakeup/acceleration
            // path; the DB is the durable queue; the timer is the safety
            // net.
            while (!stoppingToken.IsCancellationRequested)
            {
                if (_coordinator.IsPaused())
                {
                    await Task.Delay(TimeSpan.FromMilliseconds(200), stoppingToken);
                    continue;
                }
                await TryRefillFromDbAsync(stoppingToken, "idle");
                if (stoppingToken.IsCancellationRequested) break;

                // Race WaitToReadAsync with a periodic wakeup so we
                // re-poll DB even if channel stays empty. Without this,
                // a transient DB error during the previous refill leaves
                // the dispatcher blocked on WaitToReadAsync forever, and
                // any DB-only row is stranded.
                using (var wakeCts = CancellationTokenSource.CreateLinkedTokenSource(stoppingToken))
                {
                    var wakeTimer = Task.Delay(IdleWakeInterval, wakeCts.Token);
                    var waitTask = _channel.Reader.WaitToReadAsync(stoppingToken).AsTask();
                    var winner = await Task.WhenAny(waitTask, wakeTimer);
                    if (winner == waitTask)
                    {
                        // Channel has data; cancel the timer and consume.
                        wakeCts.Cancel();
                        if (!await waitTask) break;  // channel closed
                    }
                    // else: periodic wakeup, loop back to refill.
                }

                while (!stoppingToken.IsCancellationRequested
                       && !_coordinator.IsPaused()
                       && _channel.Reader.TryRead(out var flight))
                {
                    try
                    {
                        // Tri-state CAS. Transient DB failure means the
                        // row is still `pending` and should be retried
                        // on the next cycle; we must NOT mark it
                        // `completed` without ever running the agent.
                        // Fix for the 2026-08-29 review follow-up.
                        var claim = await _inbox.TryClaimAsync(flight.InboxId, stoppingToken);
                        if (claim == InboxStore.TryClaimOutcome.AlreadyClaimed)
                        {
                            _log.LogDebug("Inbox {InboxId} already claimed; skipping", flight.InboxId);
                            continue;
                        }
                        if (claim == InboxStore.TryClaimOutcome.TransientFailure)
                        {
                            _log.LogWarning(
                                "Inbox {InboxId} claim hit transient DB failure; row stays pending and will be retried in the next cycle",
                                flight.InboxId);
                            continue;
                        }
                        // Claimed. From here on, a crash leaves the row
                        // in `dispatching` and the channel slot freed.
                        // The MarkFailedAsync call in the catch below is
                        // only valid for the Claimed path; that's why we
                        // branch above before reaching this code.
                        await _coordinator.ExecuteAsync(flight.Request, flight.InboxId, stoppingToken);
                    }
                    catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
                    {
                        break;
                    }
                    catch (Exception ex)
                    {
                        // Claim succeeded (we already branched on
                        // TransientFailure) and the agent crashed.
                        // MarkFailedAsync is the right call here.
                        _log.LogError(ex, "Execution {Key} crashed in dispatcher", flight.Request.ExecutionKey);
                        try { await _inbox.MarkFailedAsync(flight.InboxId, ex.Message, CancellationToken.None); } catch { /* swallow */ }
                    }
                }
            }
        }
        catch (OperationCanceledException) { /* shutdown */ }
        _log.LogInformation("ExecutionDispatcher stopped");
    }

    /// <summary>
    /// Refill the bounded dispatch channel from the durable DB inbox.
    /// Called on every idle cycle, so a backlog larger than channel
    /// capacity eventually drains instead of being stranded at the
    /// tail. Channel-full is non-fatal: the leftover rows stay in
    /// <c>pending</c> and the next cycle picks them up.
    ///
    /// Fix for the 2026-08-29 review follow-up: the previous Dispatcher
    /// only called ListPendingAsync at startup, so rows beyond the
    /// startup channel-capacity window were never dispatched.
    /// </summary>
    private async Task TryRefillFromDbAsync(CancellationToken ct, string reason)
    {
        // Channel has data → nothing to do; the next drain iteration
        // picks it up. Cheap check so we don't hammer the DB when the
        // dispatcher is already busy.
        if (_channel.Reader.TryPeek(out _)) return;
        IReadOnlyList<InFlightExecution> pending;
        try
        {
            pending = await _inbox.ListPendingAsync(ct);
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "DB pending list failed during {Reason} refill; will retry next cycle", reason);
            return;
        }
        if (pending.Count == 0) return;
        int written = 0;
        foreach (var flight in pending)
        {
            if (!_channel.Writer.TryWrite(flight))
            {
                _log.LogInformation(
                    "Dispatch channel saturated during {Reason} refill after {Written} rows; {Remaining} DB pending will be retried in the next cycle",
                    reason, written, pending.Count - written);
                break;
            }
            written++;
        }
        if (written > 0)
        {
            _log.LogInformation(
                "{Reason} refill: pushed {Written} pending inbox rows from DB into the channel; {Remaining} remain",
                reason, written, pending.Count - written);
        }
    }
}
