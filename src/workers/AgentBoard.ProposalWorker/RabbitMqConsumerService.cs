using AgentBoard.ProposalWorker.Agents;
using AgentBoard.ProposalWorker.Execution;
using Microsoft.Extensions.Options;
using RabbitMQ.Client;
using RabbitMQ.Client.Events;

namespace AgentBoard.ProposalWorker;

/// <summary>
/// Sprint 3. Lightweight consumer: parse → enqueue to inbox → ACK → return.
/// No CLI invocation in the consumer thread. Dispatcher does the work.
///
/// Sprint 2 invariant: if inbox.TryEnqueueAsync says IsNew=false, this is a
/// redelivery and we ACK without dispatching.
/// </summary>
public sealed class RabbitMqConsumerService : BackgroundService
{
    private readonly RabbitMqOptions _mq;
    private readonly WorkerOptions _worker;
    private readonly WorkerIdentity _identity;
    private readonly InboxStore _inbox;
    private readonly ProposalMessageMapper _proposalMapper;
    private readonly WorkflowMessageMapper _workflowMapper;
    private readonly ExecutionChannel _channel;
    private readonly WorkerState _state;
    private readonly ILogger<RabbitMqConsumerService> _log;

    // 2026-08-29 review follow-up (round 8): track whether we've
    // already BasicCancel'd the direct consumer so the in-flight
    // requeue loop doesn't re-cancel. Reset on reconnect (new
    // ConsumeUntilDisconnected call gets a fresh tag).
    private bool _directConsumerCancelled;
    private string? _directConsumerTag;
    // Round-9: keep a reference to the direct AsyncEventingBasicConsumer
    // so the resume path (after a high-watermark cancel + drain) can
    // re-attach the same handler to the same channel without spinning
    // up a new consumer instance.
    private AsyncEventingBasicConsumer? _directConsumerInstance;

    // Round-10: the direct consumer cancel-resume path used to
    // only fire on a successful direct enqueue. That was a
    // dead-lock: once cancelled, the consumer never sees direct
    // deliveries, so the resume code never has a chance to
    // run. A periodic monitor (System.Threading.Timer) checks
    // the inbox count every DirectResumeCheckInterval and
    // resumes the consumer once the backlog has drained below
    // DirectResumeThreshold. The timer is started on first
    // direct-cancel and disposed on resume / channel teardown.
    private Timer? _directResumeTimer;
    private static readonly TimeSpan DirectResumeCheckInterval = TimeSpan.FromSeconds(1);

    /// <summary>
    /// Round-9 follow-up: when the direct consumer is cancelled
    /// by the high-watermark path, the consumer must come back
    /// online automatically once the inbox drains. The
    /// high-watermark is a TRANSIENT state (load spike), not
    /// degraded (operator action). We re-subscribe when the
    /// pending count drops below this fraction of
    /// <c>MaxPendingInbox</c>. Hysteresis is intentional: the
    /// threshold is well below the cap so we resume into a
    /// healthy range, not flap right back to cancelled.
    /// </summary>
    private int DirectResumeThreshold =>
        Math.Max(1, _worker.MaxPendingInbox / 2);

    public RabbitMqConsumerService(
        IOptions<RabbitMqOptions> mq,
        IOptions<WorkerOptions> worker,
        WorkerIdentity identity,
        InboxStore inbox,
        ProposalMessageMapper mapper,
        WorkflowMessageMapper workflowMapper,
        ExecutionChannel channel,
        WorkerState state,
        ILogger<RabbitMqConsumerService> log)
    {
        _mq = mq.Value;
        _worker = worker.Value;
        // Queue name must come from the same resolved worker id that the
        // server uses to route messages, otherwise we listen on the wrong
        // queue (#7 in the 2026-08-28 review).
        _identity = identity;
        _inbox = inbox;
        _proposalMapper = mapper;
        _workflowMapper = workflowMapper;
        _channel = channel;
        _state = state;
        _log = log;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        if (string.IsNullOrWhiteSpace(_mq.Uri)) { _log.LogError("RabbitMq:Uri is required; consumer is disabled"); return; }
        while (!stoppingToken.IsCancellationRequested)
        {
            try { await ConsumeUntilDisconnected(stoppingToken); }
            catch (Exception ex) when (!stoppingToken.IsCancellationRequested)
            {
                _state.LastError = ex.Message;
                _log.LogError(ex, "RabbitMQ disconnected; retrying in 5 seconds");
                await Task.Delay(TimeSpan.FromSeconds(5), stoppingToken);
            }
        }
    }

    private async Task ConsumeUntilDisconnected(CancellationToken ct)
    {
        var factory = new ConnectionFactory
        {
            Uri = new Uri(_mq.Uri),
            DispatchConsumersAsync = true,
            AutomaticRecoveryEnabled = true,
            NetworkRecoveryInterval = TimeSpan.FromSeconds(5)
        };
        using var connection = factory.CreateConnection();
        using var channel = connection.CreateModel();
        channel.ExchangeDeclare(_mq.Namespace, ExchangeType.Direct, durable: true);
        channel.ExchangeDeclare(_mq.Namespace + ".dlx", ExchangeType.Direct, durable: true);
        channel.QueueDeclare(_mq.Namespace + ".dead", durable: true, exclusive: false, autoDelete: false);
        channel.QueueBind(_mq.Namespace + ".dead", _mq.Namespace + ".dlx", "dead");
        var dlqArguments = new Dictionary<string, object>
        {
            ["x-dead-letter-exchange"] = _mq.Namespace + ".dlx",
            ["x-dead-letter-routing-key"] = "dead"
        };
        channel.QueueDeclare(_mq.PublicQueue, durable: true, exclusive: false, autoDelete: false, arguments: dlqArguments);
        channel.QueueBind(_mq.PublicQueue, _mq.Namespace, _mq.PublicRoutingKey);
        channel.ExchangeDeclare(_mq.DirectExchange, ExchangeType.Direct, durable: true);
        var directQueue = _mq.WorkerQueue(_identity.WorkerId);
        channel.QueueDeclare(directQueue, durable: true, exclusive: false, autoDelete: false, arguments: dlqArguments);
        channel.QueueBind(directQueue, _mq.DirectExchange, _mq.WorkerRoutingKey(_identity.WorkerId));
        channel.BasicQos(0, Math.Max((ushort)1, _mq.Prefetch), false);
        var done = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        connection.ConnectionShutdown += (_, _) => done.TrySetResult();
        var (publicTag, _) = Consume(channel, _mq.PublicQueue, "public", ct);
        var (directTag, directConsumer) = Consume(channel, directQueue, "direct", ct);
        _directConsumerTag = directTag;
        _directConsumerInstance = directConsumer;
        _directConsumerCancelled = false;
        using var registration = ct.Register(() => done.TrySetResult());
        await done.Task;
        // Round-10: stop the resume monitor BEFORE we discard
        // _directConsumerTag / _directConsumerInstance. The
        // monitor holds a closure over the channel reference
        // and would otherwise fire one last time on a dead
        // channel after teardown.
        StopDirectResumeMonitor();
        _directConsumerTag = null;
        _directConsumerInstance = null;
        try { channel.BasicCancel(publicTag); channel.BasicCancel(directTag); } catch { }
    }

    private (string tag, AsyncEventingBasicConsumer consumer) Consume(IModel channel, string queue, string source, CancellationToken stoppingToken)
    {
        var consumer = new AsyncEventingBasicConsumer(channel);
        consumer.Received += async (_, eventArgs) =>
        {
            try
            {
                if (_state.Paused)
                {
                    // Keep the message; requeue so another consumer (or this
                    // one after resume) can pick it up.
                    await Task.Delay(TimeSpan.FromSeconds(1), stoppingToken);
                    channel.BasicNack(eventArgs.DeliveryTag, false, true);
                    return;
                }

                // 2026-08-29 review follow-up: when the worker is
                // degraded (non-recoverable DB error already
                // observed), the Dispatcher has stopped scheduling
                // new work. The consumer must stop accepting new
                // messages too — otherwise it would still pull
                // from the public RabbitMQ queue, write rows into
                // the local inbox, and ACK, "stealing" tasks from
                // healthy peer workers in a multi-worker deploy.
                // Requeue (not DLQ) so a healthy peer picks it up
                // immediately. We also pause briefly to avoid a
                // tight requeue/ACK loop in the consumer thread.
                //
                // 2026-08-29 review follow-up (round 8):
                // differential handling for public vs direct queue.
                // The public queue has healthy peers that can
                // absorb the message, so NACK requeue is correct.
                // The direct queue has only this worker as a
                // consumer — NACK requeue creates a tight
                // consume-NACK-consume loop on the same worker
                // (CPU+log burn). For the direct queue we
                // BasicCancel the consumer ONCE on first degraded
                // message, so Rabbit stops delivering and the
                // in-flight message is NACK-requeued back to
                // the (now empty) queue where it waits for an
                // operator to clear the degraded flag.
                if (_state.IsDegraded)
                {
                    if (source == "direct")
                    {
                        CancelDirectConsumerAsync(channel, "degraded");
                        _log.LogWarning(
                            "Worker is degraded ({Reason}); direct consumer cancelled; NACK-requeue in-flight message so Rabbit holds it until recovery",
                            _state.DegradedReason);
                        await Task.Delay(TimeSpan.FromSeconds(1), stoppingToken);
                        channel.BasicNack(eventArgs.DeliveryTag, false, true);
                    }
                    else
                    {
                        _log.LogWarning(
                            "Worker is degraded ({Reason}); refusing to consume from {Queue}, requeuing message so a healthy peer can take it",
                            _state.DegradedReason, queue);
                        await Task.Delay(TimeSpan.FromSeconds(1), stoppingToken);
                        channel.BasicNack(eventArgs.DeliveryTag, false, true);
                    }
                    return;
                }

                // Sprint 12 (Generic AgentWorker): the queue may carry both
                // legacy proposal payloads (proposal_id field) AND workflow
                // events (event field). The discriminator in WorkloadMessage.Parse
                // picks the right parser. The downstream ExecutionRequest is the
                // same shape for both; the dispatcher only cares about the
                // WorkloadType string.
                WorkloadMessage message;
                try
                {
                    message = WorkloadMessage.Parse(eventArgs.Body);
                }
                catch (InvalidDataException ex)
                {
                    // Poison: not proposal and not workflow. DLQ instead of looping.
                    _log.LogWarning(ex, "Poison workload message on {Queue}", queue);
                    channel.BasicNack(eventArgs.DeliveryTag, false, false);
                    return;
                }

                ExecutionRequest request;
                try
                {
                    request = message switch
                    {
                        WorkloadMessage.Proposal p => _proposalMapper.MapToExecution(p.Inner, source),
                        WorkloadMessage.Workflow w => _workflowMapper.MapToExecution(w.Inner, source),
                        _ => throw new InvalidOperationException(
                            $"unreachable workload variant: {message.GetType().Name}"),
                    };
                }
                catch (InvalidAgentException ex)
                {
                    // Poison: agent not registered. DLQ instead of looping.
                    _log.LogError(ex, "Dropping message for unregistered agent {Agent}", ex.AgentType);
                    channel.BasicNack(eventArgs.DeliveryTag, false, false);
                    return;
                }

                // 2026-08-29 review follow-up (round 9): the
                // round-8 high-watermark path inserted the
                // inbox row FIRST and then marked it failed
                // on overflow. That left a terminal dedupe
                // record on disk, so when the NACK-requeued
                // Rabbit message came back to the same worker
                // INSERT OR IGNORE matched the existing
                // execution_key, IsNew=false, and the consumer
                // ACK-dropped the redelivery. The task was
                // silently lost. For the direct queue (only
                // this worker consumes) the loss is
                // deterministic. Round-9 fix: count + insert
                // inside ONE transaction. On overflow, NO row
                // is produced and the Rabbit message is
                // NACK-requeued back to the broker where it
                // sits safely until the inbox drains.
                var (enqueueOutcome, inboxId) = await _inbox.TryEnqueueWithinCapacityAsync(
                    request, _worker.MaxPendingInbox, stoppingToken);

                switch (enqueueOutcome)
                {
                    case InboxStore.EnqueueWithinCapacityOutcome.Duplicate:
                        // Sprint 2: idempotency hit. ACK and drop.
                        _log.LogDebug("Duplicate {Key}; ACK without dispatch", request.ExecutionKey);
                        channel.BasicAck(eventArgs.DeliveryTag, false);
                        return;

                    case InboxStore.EnqueueWithinCapacityOutcome.CapacityExceeded:
                        // Pending inbox at/above MaxPendingInbox.
                        // The round-9 fix: no row was inserted, so
                        // the Rabbit message must be NACK-requeued
                        // to a peer (or to us later) — the broker
                        // is the only safe place to hold it. For
                        // the direct queue, also cancel the
                        // consumer (see ResumeDirectConsumerAsync
                        // for the auto-resume when the inbox
                        // drains). For the public queue, healthy
                        // peers can take the message immediately.
                        _log.LogWarning(
                            "Pending inbox at/above MaxPendingInbox {Limit}; refusing to enqueue {Key} on {Queue} (NACK-requeue, no dedupe row left behind)",
                            _worker.MaxPendingInbox, request.ExecutionKey, queue);
                        if (source == "direct")
                        {
                            CancelDirectConsumerAsync(channel,
                                "high-watermark");
                        }
                        channel.BasicNack(eventArgs.DeliveryTag, false, true);
                        return;

                    case InboxStore.EnqueueWithinCapacityOutcome.Enqueued:
                        break;
                }

                // If the direct consumer was previously cancelled
                // by a high-watermark event, check whether the
                // backlog has drained enough to safely resume. This
                // is the round-9 review follow-up: the round-8
                // code only ever set _directConsumerCancelled =
                // true, with no symmetric resume path. After a
                // high-watermark cancel the direct queue stayed
                // dead until the worker restarted, even after the
                // inbox had drained back to a healthy level. The
                // resume check runs on every successful enqueue
                // (cheap single COUNT query) so backlog churn
                // naturally brings the consumer back online.
                if (source == "direct" && _directConsumerCancelled
                    && _worker.MaxPendingInbox > 0)
                {
                    var pending = await _inbox.CountPendingAsync(stoppingToken);
                    if (pending < DirectResumeThreshold)
                    {
                        ResumeDirectConsumerAsync(channel,
                            $"pending {pending} < {DirectResumeThreshold}");
                    }
                }

                // Hand off a wake-signal sentinel to the dispatcher.
                // The DB inbox already holds the (request, inboxId)
                // pair (inserted above); the dispatcher will read it
                // from the DB on the next wake. This is the
                // DB-first scheduling architecture — the bounded
                // channel carries only a sentinel, not the work
                // payload, so a permanently busy channel can never
                // starve DB-only pending rows. 2026-08-29 review
                // follow-up (round 7).
                await _channel.Writer.WriteAsync(
                    new WakeSignal { At = DateTimeOffset.UtcNow, Source = "rabbit" },
                    stoppingToken);
                channel.BasicAck(eventArgs.DeliveryTag, false);
            }
            // InvalidDataException is handled inline above (the parser
            // path) so we no longer need a top-level catch here; the
            // only remaining escalation path is the generic Exception.
            catch (Exception ex)
            {
                _state.LastError = ex.Message;
                _log.LogError(ex, "Unhandled consumer error on {Queue}", queue);
                channel.BasicNack(eventArgs.DeliveryTag, false, true);
            }
        };
        var tag = channel.BasicConsume(queue, autoAck: false, consumer);
        return (tag, consumer);
    }

    /// <summary>
    /// BasicCancel the direct consumer (once, idempotent) and
    /// record the cancelled state. Round-9: the previous
    /// code inlined this in the degraded branch; factored out
    /// so the high-watermark branch can share the same
    /// cancellation primitive without diverging.
    ///
    /// Round-10: also starts the resume monitor. The high-
    /// watermark path can leave the direct consumer cancelled
    /// even after the inbox drains; the monitor re-attaches
    /// the consumer once pending count drops below
    /// <see cref="DirectResumeThreshold"/>. Without the
    /// monitor the direct queue stays dead until the worker
    /// restarts (or a direct delivery happens to sneak in
    /// before the cancel, which the design cannot rely on).
    /// </summary>
    private void CancelDirectConsumerAsync(IModel channel, string reason)
    {
        if (_directConsumerCancelled) return;
        _directConsumerCancelled = true;
        try
        {
            if (_directConsumerTag is not null)
            {
                channel.BasicCancel(_directConsumerTag);
                _log.LogWarning(
                    "Direct consumer cancelled (reason={Reason}); messages stay on Rabbit until resume",
                    reason);
            }
        }
        catch (Exception cancelEx)
        {
            _log.LogWarning(cancelEx,
                "BasicCancel of direct consumer failed (reason={Reason}); will continue to NACK-requeue",
                reason);
        }
        StartDirectResumeMonitor(channel, reason);
    }

    /// <summary>
    /// Start (or no-op if already running) a periodic timer
    /// that checks the inbox count and resumes the direct
    /// consumer once the backlog is below the hysteresis
    /// threshold. The timer captures the channel by value;
    /// when the channel is torn down the timer fires one
    /// last time and bails on the next reconnect.
    /// </summary>
    private void StartDirectResumeMonitor(IModel channel, string reason)
    {
        if (_directResumeTimer is not null) return;
        _directResumeTimer = new Timer(_ =>
        {
            try
            {
                if (!_directConsumerCancelled) return;
                if (_directConsumerInstance is null || _directConsumerTag is null) return;
                // Cheap indexed count; safe to run on the timer thread.
                var pending = _inbox.CountPendingAsync(CancellationToken.None)
                    .ConfigureAwait(false).GetAwaiter().GetResult();
                if (pending < DirectResumeThreshold)
                {
                    ResumeDirectConsumerAsync(channel,
                        $"pending {pending} < {DirectResumeThreshold} (round-10 monitor, original reason={reason})");
                }
            }
            catch (Exception ex)
            {
                _log.LogWarning(ex, "Direct resume monitor tick failed; will retry next interval");
            }
        }, state: null, dueTime: DirectResumeCheckInterval, period: DirectResumeCheckInterval);
        _log.LogInformation("Direct resume monitor started (every {Interval}s) after cancel reason={Reason}",
            DirectResumeCheckInterval.TotalSeconds, reason);
    }

    /// <summary>
    /// Stop the resume monitor. Called when a successful
    /// resume happens, on channel teardown, or on host stop.
    /// </summary>
    private void StopDirectResumeMonitor()
    {
        if (_directResumeTimer is null) return;
        try { _directResumeTimer.Dispose(); }
        catch { /* best-effort */ }
        _directResumeTimer = null;
        _log.LogDebug("Direct resume monitor stopped");
    }

    /// <summary>
    /// Re-subscribe the direct consumer after a high-watermark
    /// cancel. Round-9 review follow-up: the round-8 design
    /// had no resume path; the direct queue stayed dead until
    /// the worker restarted. We keep the original
    /// <see cref="IConsumer"/> instance alive across the cancel
    /// and re-attach it to the channel here when the inbox
    /// drains. The cancellation flag is reset only on a
    /// successful re-attach; if the channel is already torn
    /// down (e.g. mid-reconnect) the next
    /// <c>ConsumeUntilDisconnected</c> will recreate the
    /// consumer and the flag is irrelevant.
    /// </summary>
    private void ResumeDirectConsumerAsync(IModel channel, string reason)
    {
        if (!_directConsumerCancelled) return;
        if (_directConsumerInstance is null) return;  // not yet attached
        if (_directConsumerTag is null) return;
        try
        {
            var newTag = channel.BasicConsume(
                _mq.WorkerQueue(_identity.WorkerId),
                autoAck: false,
                _directConsumerInstance);
            _directConsumerTag = newTag;
            _directConsumerCancelled = false;
            _log.LogInformation(
                "Direct consumer resumed (reason={Reason}); new tag={Tag}",
                reason, newTag);
            // Round-10: stop the resume monitor once a successful
            // resume lands. The monitor is only useful while the
            // consumer is cancelled; once attached it's pure
            // overhead.
            StopDirectResumeMonitor();
        }
        catch (Exception resumeEx)
        {
            _log.LogError(resumeEx,
                "Direct consumer resume failed; flag stays set so the next monitor tick may succeed");
            // Do NOT reset _directConsumerCancelled here; the
            // timer will keep firing at DirectResumeCheckInterval
            // and try again.
        }
    }
}
