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
    private readonly ProposalMessageMapper _mapper;
    private readonly ExecutionChannel _channel;
    private readonly WorkerState _state;
    private readonly ILogger<RabbitMqConsumerService> _log;

    // 2026-08-29 review follow-up (round 8): track whether we've
    // already BasicCancel'd the direct consumer so the in-flight
    // requeue loop doesn't re-cancel. Reset on reconnect (new
    // ConsumeUntilDisconnected call gets a fresh tag).
    private bool _directConsumerCancelled;
    private string? _directConsumerTag;

    public RabbitMqConsumerService(
        IOptions<RabbitMqOptions> mq,
        IOptions<WorkerOptions> worker,
        WorkerIdentity identity,
        InboxStore inbox,
        ProposalMessageMapper mapper,
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
        _mapper = mapper;
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
        var publicTag = Consume(channel, _mq.PublicQueue, "public", ct);
        var directTag = Consume(channel, directQueue, "direct", ct);
        _directConsumerTag = directTag;
        _directConsumerCancelled = false;
        using var registration = ct.Register(() => done.TrySetResult());
        await done.Task;
        _directConsumerTag = null;
        try { channel.BasicCancel(publicTag); channel.BasicCancel(directTag); } catch { }
    }

    private string Consume(IModel channel, string queue, string source, CancellationToken stoppingToken)
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
                        if (!_directConsumerCancelled)
                        {
                            _directConsumerCancelled = true;
                            try
                            {
                                if (_directConsumerTag is not null)
                                {
                                    channel.BasicCancel(_directConsumerTag);
                                }
                            }
                            catch (Exception cancelEx)
                            {
                                _log.LogWarning(cancelEx,
                                    "BasicCancel of direct consumer failed; will continue to NACK-requeue");
                            }
                        }
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

                var message = ProposalMessage.Parse(eventArgs.Body);
                ExecutionRequest request;
                try
                {
                    request = _mapper.MapToExecution(message, source);
                }
                catch (InvalidAgentException ex)
                {
                    // Poison: agent not registered. DLQ instead of looping.
                    _log.LogError(ex, "Dropping message for unregistered agent {Agent}", ex.AgentType);
                    channel.BasicNack(eventArgs.DeliveryTag, false, false);
                    return;
                }

                var (inboxId, isNew) = await _inbox.TryEnqueueAsync(request, stoppingToken);
                if (!isNew)
                {
                    // Sprint 2: idempotency hit. ACK and drop.
                    _log.LogDebug("Duplicate {Key}; ACK without dispatch", request.ExecutionKey);
                    channel.BasicAck(eventArgs.DeliveryTag, false);
                    return;
                }

                // 2026-08-29 review follow-up (round 8):
                // Worker.MaxPendingInbox high-watermark. The
                // previous DropWrite-only strategy traded
                // channel-level blocking for unbounded local
                // SQLite growth + ACK stealing in multi-worker
                // deploys. With DropWrite the consumer never
                // blocks, so the local inbox can grow without
                // bound. Enforce a hard cap: if pending count
                // exceeds MaxPendingInbox after enqueue, mark the
                // freshly-inserted row as failed (so the
                // dispatcher doesn't keep re-claiming it) and
                // NACK-requeue the Rabbit message. For the
                // direct queue, also BasicCancel the consumer so
                // we don't burn CPU+Rabbit traffic in a hot
                // redelivery loop until the operator intervenes.
                if (_worker.MaxPendingInbox > 0)
                {
                    var pending = await _inbox.CountPendingAsync(stoppingToken);
                    if (pending > _worker.MaxPendingInbox)
                    {
                        _log.LogWarning(
                            "Pending inbox {Pending} exceeded MaxPendingInbox {Limit} after enqueue of {Key}; refusing to accept more on {Queue}",
                            pending, _worker.MaxPendingInbox, request.ExecutionKey, queue);
                        try
                        {
                            await _inbox.MarkFailedAsync(inboxId,
                                $"pending inbox {pending} > MaxPendingInbox {_worker.MaxPendingInbox}; refused",
                                CancellationToken.None);
                        }
                        catch (Exception markEx)
                        {
                            _log.LogError(markEx,
                                "Failed to mark inbox {InboxId} as failed under high-watermark; row stays pending and will be re-claimed",
                                inboxId);
                        }
                        if (source == "direct" && !_directConsumerCancelled)
                        {
                            _directConsumerCancelled = true;
                            try
                            {
                                if (_directConsumerTag is not null)
                                {
                                    channel.BasicCancel(_directConsumerTag);
                                }
                            }
                            catch (Exception cancelEx)
                            {
                                _log.LogWarning(cancelEx,
                                    "BasicCancel of direct consumer under high-watermark failed");
                            }
                        }
                        channel.BasicNack(eventArgs.DeliveryTag, false, true);
                        return;
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
            catch (InvalidDataException ex)
            {
                _log.LogWarning(ex, "Poison proposal message on {Queue}", queue);
                channel.BasicNack(eventArgs.DeliveryTag, false, false);
            }
            catch (Exception ex)
            {
                _state.LastError = ex.Message;
                _log.LogError(ex, "Unhandled consumer error on {Queue}", queue);
                channel.BasicNack(eventArgs.DeliveryTag, false, true);
            }
        };
        return channel.BasicConsume(queue, autoAck: false, consumer);
    }
}
