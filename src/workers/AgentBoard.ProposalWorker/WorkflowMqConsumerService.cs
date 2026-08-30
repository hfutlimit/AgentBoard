using AgentBoard.ProposalWorker.Agents;
using AgentBoard.ProposalWorker.Execution;
using Microsoft.Extensions.Options;
using RabbitMQ.Client;
using RabbitMQ.Client.Events;

namespace AgentBoard.ProposalWorker;

/// <summary>
/// Sprint 12 (Generic AgentWorker). Consumes the
/// <c>agentboard.workflow</c> RabbitMQ namespace so the worker can
/// react to <c>task.available</c>, <c>task.review_requested</c>,
/// <c>task.rejected</c>, <c>proposal.ticket_requested</c>, and
/// friends — the events that close the orchestration gap the
/// 2026-08-30 review flagged.
///
/// Topology mirrors the FastAPI <c>WorkflowTopology</c> so the two
/// stacks agree on queue / exchange names:
/// <list type="bullet">
///   <item>Exchange <c>{ns}</c> (topic, durable)</item>
///   <item>Queue <c>{ns}.broadcast</c> bound to <c>workflow.broadcast.#</c></item>
///   <item>Queue <c>{ns}.agent.{workerId}</c> bound to
///         <c>workflow.agent.{workerId}</c></item>
///   <item>DLX <c>{ns}.dlx</c> + dead-letter queue <c>{ns}.dead</c></item>
/// </list>
///
/// Design choices (intentional simplifications vs the proposal consumer):
/// <list type="bullet">
///   <item><b>One consumer per queue</b>, no BasicCancel / high-watermark
///         resume dance. The dispatcher (which is the real backpressure
///         gate via <c>MaxPendingInbox</c>) decides when the worker is
///         too busy. We model capacity the same way as the proposal
///         consumer: try enqueue, NACK-requeue on overflow, ACK on
///         Duplicate / Enqueued.</item>
///   <item><b>No degraded branch.</b> The proposal consumer cancels the
///         direct consumer when degraded so a healthy peer can absorb
///         the message. The workflow consumer is an additive path: if
///         the worker is degraded, NACK-requeue so a healthy peer can
///         take the broadcast. We add the cancel path in a follow-up
///         if production shows it matters.</item>
///   <item><b>No envelope discrimination</b>. Workflow payloads are
///         always <see cref="WorkflowMessage"/>; the
///         <see cref="WorkloadMessage"/> envelope is for the proposal
///         queue where two message shapes co-exist.</item>
/// </list>
///
/// Disabled by setting <c>RabbitMq:WorkflowConsumerEnabled=false</c>
/// in appsettings. The host stays up and the proposal consumer keeps
/// running; the workflow branch is silent.
/// </summary>
public sealed class WorkflowMqConsumerService : BackgroundService
{
    private readonly RabbitMqOptions _mq;
    private readonly WorkerOptions _worker;
    private readonly WorkerIdentity _identity;
    private readonly InboxStore _inbox;
    private readonly WorkflowMessageMapper _mapper;
    private readonly ExecutionChannel _channel;
    private readonly WorkerState _state;
    private readonly ILogger<WorkflowMqConsumerService> _log;

    public WorkflowMqConsumerService(
        IOptions<RabbitMqOptions> mq,
        IOptions<WorkerOptions> worker,
        WorkerIdentity identity,
        InboxStore inbox,
        WorkflowMessageMapper mapper,
        ExecutionChannel channel,
        WorkerState state,
        ILogger<WorkflowMqConsumerService> log)
    {
        _mq = mq.Value;
        _worker = worker.Value;
        _identity = identity;
        _inbox = inbox;
        _mapper = mapper;
        _channel = channel;
        _state = state;
        _log = log;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        if (!_mq.WorkflowConsumerEnabled)
        {
            _log.LogInformation(
                "Workflow consumer disabled by config (RabbitMq:WorkflowConsumerEnabled=false); not subscribing to {Ns}",
                _mq.WorkflowNamespace);
            return;
        }
        if (string.IsNullOrWhiteSpace(_mq.Uri))
        {
            _log.LogError("RabbitMq:Uri is required; workflow consumer is disabled");
            return;
        }
        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                await ConsumeUntilDisconnected(stoppingToken);
            }
            catch (Exception ex) when (!stoppingToken.IsCancellationRequested)
            {
                _state.LastError = ex.Message;
                _log.LogError(ex, "Workflow RabbitMQ disconnected; retrying in 5 seconds");
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
            NetworkRecoveryInterval = TimeSpan.FromSeconds(5),
        };
        using var connection = factory.CreateConnection();
        using var channel = connection.CreateModel();

        // Dead-letter wiring: identical to the proposal consumer so an
        // operator can mix and match without re-learning.
        channel.ExchangeDeclare(_mq.WorkflowDlxExchange, ExchangeType.Direct, durable: true);
        channel.QueueDeclare(_mq.WorkflowDeadQueue, durable: true, exclusive: false, autoDelete: false);
        channel.QueueBind(_mq.WorkflowDeadQueue, _mq.WorkflowDlxExchange, "dead");
        var dlqArgs = new Dictionary<string, object>
        {
            ["x-dead-letter-exchange"] = _mq.WorkflowDlxExchange,
            ["x-dead-letter-routing-key"] = "dead",
        };

        // Topic exchange matches FastAPI WorkflowTopology.exchange.
        channel.ExchangeDeclare(_mq.WorkflowNamespace, ExchangeType.Topic, durable: true);

        // Broadcast queue: catches every workflow.broadcast.* event.
        // The .NET worker acts as one of N subscribers; whichever worker
        // wins the inbox INSERT (CAS via execution_key) gets the work.
        var broadcastQueue = _mq.WorkflowBroadcastQueue;
        channel.QueueDeclare(broadcastQueue, durable: true, exclusive: false, autoDelete: false, arguments: dlqArgs);
        channel.QueueBind(broadcastQueue, _mq.WorkflowNamespace, _mq.WorkflowBroadcastPattern);

        // Per-worker direct queue: events addressed to this specific
        // worker (workflow.agent.{workerId}). Mirrors the proposal
        // direct queue so requeue-then-drain backpressure behaves the
        // same way.
        var agentQueue = _mq.WorkflowAgentQueue(_identity.WorkerId);
        var agentRoutingKey = _mq.WorkflowAgentPattern + _identity.WorkerId;
        channel.QueueDeclare(agentQueue, durable: true, exclusive: false, autoDelete: false, arguments: dlqArgs);
        channel.QueueBind(agentQueue, _mq.WorkflowNamespace, agentRoutingKey);

        channel.BasicQos(0, Math.Max((ushort)1, _mq.Prefetch), false);

        var done = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        connection.ConnectionShutdown += (_, _) => done.TrySetResult();

        var broadcastTag = Consume(channel, broadcastQueue, "broadcast", ct);
        var directTag = Consume(channel, agentQueue, "agent", ct);

        using var registration = ct.Register(() => done.TrySetResult());
        _log.LogInformation(
            "Workflow consumer subscribed: ns={Ns} broadcast={Bq} agent={Aq} (worker={Wid})",
            _mq.WorkflowNamespace, broadcastQueue, agentQueue, _identity.WorkerId);
        await done.Task;
        try { channel.BasicCancel(broadcastTag); channel.BasicCancel(directTag); } catch { }
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
                    await Task.Delay(TimeSpan.FromSeconds(1), stoppingToken);
                    channel.BasicNack(eventArgs.DeliveryTag, false, true);
                    return;
                }

                // Degraded: the proposal consumer cancels the direct
                // consumer to break the tight loop. For workflow we
                // use the lighter "NACK-requeue, peer may take" path —
                // if a healthy peer is online the broker hands the
                // broadcast off naturally. If we are alone, the
                // message stays in the queue and waits for us to
                // recover (operator clears the degraded flag).
                if (_state.IsDegraded)
                {
                    _log.LogWarning(
                        "Worker is degraded ({Reason}); workflow {Source} message requeued, waiting for recovery",
                        _state.DegradedReason, source);
                    await Task.Delay(TimeSpan.FromSeconds(1), stoppingToken);
                    channel.BasicNack(eventArgs.DeliveryTag, false, true);
                    return;
                }

                WorkflowMessage message;
                try
                {
                    message = WorkflowMessage.Parse(eventArgs.Body);
                }
                catch (InvalidDataException ex)
                {
                    // Poison: not a workflow payload. DLQ instead of looping.
                    _log.LogWarning(ex, "Poison workflow message on {Queue}", queue);
                    channel.BasicNack(eventArgs.DeliveryTag, false, false);
                    return;
                }

                ExecutionRequest request;
                try
                {
                    request = _mapper.MapToExecution(message, source);
                }
                catch (InvalidAgentException ex)
                {
                    _log.LogError(ex,
                        "Dropping workflow message for unregistered agent {Agent} (event={Event})",
                        ex.AgentType, message.Event);
                    channel.BasicNack(eventArgs.DeliveryTag, false, false);
                    return;
                }
                catch (InvalidDataException ex)
                {
                    // Event not in the routing table (e.g. comment.replied,
                    // review.vote_cast). The .NET worker has nothing to
                    // execute for these; drop to DLQ rather than loop.
                    _log.LogInformation(
                        "Skipping non-actionable workflow event {Event} (entity={Et}#{Eid}): {Reason}",
                        message.Event, message.EntityType, message.EntityId, ex.Message);
                    channel.BasicNack(eventArgs.DeliveryTag, false, false);
                    return;
                }

                // Capacity gate: identical semantics to the proposal
                // consumer. On overflow we NACK-requeue; the dispatcher
                // (and other workers) eventually drain.
                var (outcome, _) = await _inbox.TryEnqueueWithinCapacityAsync(
                    request, _worker.MaxPendingInbox, stoppingToken);
                switch (outcome)
                {
                    case InboxStore.EnqueueWithinCapacityOutcome.Duplicate:
                        _log.LogDebug("Duplicate workflow {Key}; ACK without dispatch", request.ExecutionKey);
                        channel.BasicAck(eventArgs.DeliveryTag, false);
                        return;
                    case InboxStore.EnqueueWithinCapacityOutcome.CapacityExceeded:
                        _log.LogWarning(
                            "Pending inbox at/above {Limit}; requeueing workflow {Key} on {Queue}",
                            _worker.MaxPendingInbox, request.ExecutionKey, queue);
                        channel.BasicNack(eventArgs.DeliveryTag, false, true);
                        return;
                }

                // Wake the dispatcher; the inbox row is the durable hand-off.
                await _channel.Writer.WriteAsync(
                    new WakeSignal { At = DateTimeOffset.UtcNow, Source = $"workflow:{source}" },
                    stoppingToken);
                channel.BasicAck(eventArgs.DeliveryTag, false);
            }
            catch (Exception ex) when (!stoppingToken.IsCancellationRequested)
            {
                _state.LastError = ex.Message;
                _log.LogError(ex, "Unhandled workflow consumer error on {Queue}", queue);
                channel.BasicNack(eventArgs.DeliveryTag, false, true);
            }
        };
        return channel.BasicConsume(queue, autoAck: false, consumer);
    }
}
