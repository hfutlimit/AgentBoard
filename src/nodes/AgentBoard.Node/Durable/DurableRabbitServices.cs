// SPDX-License-Identifier: MIT
using System.Text;
using System.Text.Json;
using AgentBoard.Contracts;
using Microsoft.Extensions.Options;
using RabbitMQ.Client;
using RabbitMQ.Client.Events;

namespace AgentBoard.Node.Durable;

public sealed class DurableRabbitResultTransport : IResultTransport
{
    private readonly string _brokerUri;
    private readonly TimeSpan _confirmTimeout;

    public DurableRabbitResultTransport(string brokerUri, TimeSpan? confirmTimeout = null)
    {
        _brokerUri = brokerUri;
        _confirmTimeout = confirmTimeout ?? TimeSpan.FromSeconds(5);
    }

    public BrokerConfirm Publish(LocalOutboxRecord record)
    {
        if (!Uri.TryCreate(_brokerUri, UriKind.Absolute, out var brokerUri))
        {
            throw new InvalidOperationException("RabbitMq:Uri is required for durable result publishing");
        }

        var factory = new ConnectionFactory { Uri = brokerUri, AutomaticRecoveryEnabled = false };
        using var connection = factory.CreateConnection();
        using var channel = connection.CreateModel();
        DeclareCommonTopology(channel);
        channel.ConfirmSelect();

        var properties = channel.CreateBasicProperties();
        properties.Persistent = true;
        properties.ContentType = "application/json";
        properties.Type = record.Result.MessageType;
        properties.MessageId = record.MessageId;
        properties.CorrelationId = record.Result.CorrelationId;
        properties.Headers = new Dictionary<string, object>
        {
            ["schema_version"] = Encoding.UTF8.GetBytes(record.Result.SchemaVersion),
            ["traceparent"] = Encoding.UTF8.GetBytes(record.Result.Traceparent!),
        };

        channel.BasicPublish(DurableMessaging.ResultExchange, DurableMessaging.ResultRoutingKey,
            mandatory: true, basicProperties: properties,
            body: Encoding.UTF8.GetBytes(JsonSerializer.Serialize(record.Result, ContractJson.Options)));
        return channel.WaitForConfirms(_confirmTimeout)
            ? BrokerConfirm.Confirmed
            : BrokerConfirm.Failed;
    }

    internal static void DeclareCommonTopology(IModel channel)
    {
        channel.ExchangeDeclare(DurableMessaging.CommandExchange, ExchangeType.Direct, durable: true);
        channel.ExchangeDeclare(DurableMessaging.ResultExchange, ExchangeType.Direct, durable: true);
        channel.ExchangeDeclare(DurableMessaging.DeadLetterExchange, ExchangeType.Direct, durable: true);
        channel.QueueDeclare(DurableMessaging.ServerResultQueue, durable: true, exclusive: false, autoDelete: false,
            arguments: DeadLetterArguments());
        channel.QueueBind(DurableMessaging.ServerResultQueue,
            DurableMessaging.ResultExchange, DurableMessaging.ResultRoutingKey);
        channel.QueueDeclare(DurableMessaging.DeadLetterQueue, durable: true, exclusive: false, autoDelete: false);
        channel.QueueBind(DurableMessaging.DeadLetterQueue,
            DurableMessaging.DeadLetterExchange, DurableMessaging.DeadLetterRoutingKey);
    }

    internal static Dictionary<string, object> DeadLetterArguments() => new()
    {
        ["x-dead-letter-exchange"] = DurableMessaging.DeadLetterExchange,
        ["x-dead-letter-routing-key"] = DurableMessaging.DeadLetterRoutingKey,
    };
}

/// <summary>
/// Real command consumer for the Target-v1 envelopes. It ACKs only after the
/// SQLite journal accepts the command, then invokes the provider runner. Poison
/// messages go to the durable DLQ; pending journal rows replay on restart.
/// </summary>
public sealed class DurableCommandConsumerService : BackgroundService
{
    private readonly DurableExecutionOptions _options;
    private readonly RabbitMqOptions _rabbit;
    private readonly WorkerIdentity _identity;
    private readonly IServiceProvider _services;
    private DurableAssignmentRunner _runner = null!;
    private LocalResultOutbox _outbox = null!;
    private readonly ILogger<DurableCommandConsumerService> _log;
    private readonly HashSet<string> _running = new(StringComparer.Ordinal);
    private readonly object _gate = new();

    public DurableCommandConsumerService(
        IOptions<DurableExecutionOptions> options,
        IOptions<RabbitMqOptions> rabbit,
        WorkerIdentity identity,
        IServiceProvider services,
        ILogger<DurableCommandConsumerService> log)
    {
        _options = options.Value;
        _rabbit = rabbit.Value;
        _identity = identity;
        _services = services;
        _log = log;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        if (!_options.Enabled)
        {
            _log.LogInformation("Target-v1 durable execution consumer is disabled");
            return;
        }

        if (!Uri.TryCreate(_rabbit.Uri, UriKind.Absolute, out _))
        {
            throw new InvalidOperationException("RabbitMq:Uri is required when DurableExecution:Enabled=true");
        }

        _runner = _services.GetRequiredService<DurableAssignmentRunner>();
        _outbox = _services.GetRequiredService<LocalResultOutbox>();
        _runner.RebuildAssignments();
        StartPending(stoppingToken);

        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                await ConsumeUntilDisconnected(stoppingToken);
            }
            catch (Exception error) when (!stoppingToken.IsCancellationRequested)
            {
                _log.LogError(error, "Durable command consumer disconnected; retrying");
                await Task.Delay(TimeSpan.FromSeconds(5), stoppingToken);
            }
        }
    }

    private async Task ConsumeUntilDisconnected(CancellationToken cancellationToken)
    {
        var factory = new ConnectionFactory
        {
            Uri = new Uri(_rabbit.Uri),
            DispatchConsumersAsync = true,
            AutomaticRecoveryEnabled = true,
            NetworkRecoveryInterval = TimeSpan.FromSeconds(5),
        };
        using var connection = factory.CreateConnection();
        using var channel = connection.CreateModel();
        DurableRabbitResultTransport.DeclareCommonTopology(channel);
        var queue = DurableMessaging.WorkerCommandQueue(_identity.WorkerId);
        channel.QueueDeclare(queue, durable: true, exclusive: false, autoDelete: false,
            arguments: DurableRabbitResultTransport.DeadLetterArguments());
        channel.QueueBind(queue, DurableMessaging.CommandExchange,
            DurableMessaging.WorkerRoutingKey(_identity.WorkerId));
        channel.BasicQos(0, Math.Max((ushort)1, _options.Prefetch), global: false);

        var stopped = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        connection.ConnectionShutdown += (_, _) => stopped.TrySetResult();
        var consumer = new AsyncEventingBasicConsumer(channel);
        consumer.Received += async (_, delivery) =>
        {
            CommandEnvelope? command;
            try
            {
                command = JsonSerializer.Deserialize<CommandEnvelope>(delivery.Body.Span, ContractJson.Options);
            }
            catch (JsonException)
            {
                channel.BasicNack(delivery.DeliveryTag, multiple: false, requeue: false);
                return;
            }

            if (command is null)
            {
                channel.BasicNack(delivery.DeliveryTag, multiple: false, requeue: false);
                return;
            }

            var acceptance = _runner.Accept(command);
            if (acceptance.ShouldAckBroker)
            {
                channel.BasicAck(delivery.DeliveryTag, multiple: false);
                if (acceptance.Kind == AcceptanceKind.Accepted)
                {
                    Start(command, cancellationToken);
                }
                return;
            }

            // Schema/misrouting failures cannot heal by immediate redelivery.
            channel.BasicNack(delivery.DeliveryTag, multiple: false, requeue: false);
            await Task.CompletedTask;
        };

        var tag = channel.BasicConsume(queue, autoAck: false, consumer);
        using var connectionLoop = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        using var registration = cancellationToken.Register(() => stopped.TrySetResult());
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(2));
        var maintenance = Task.Run(async () =>
        {
            while (await timer.WaitForNextTickAsync(connectionLoop.Token))
            {
                _outbox.Drain();
                StartPending(connectionLoop.Token);
            }
        }, connectionLoop.Token);

        await stopped.Task;
        connectionLoop.Cancel();
        try { channel.BasicCancel(tag); } catch { }
        try { await maintenance; } catch (OperationCanceledException) { }
    }

    private void StartPending(CancellationToken cancellationToken)
    {
        foreach (var command in _runner.PendingCommands())
        {
            Start(command, cancellationToken);
        }
    }

    private void Start(CommandEnvelope command, CancellationToken cancellationToken)
    {
        lock (_gate)
        {
            if (!_running.Add(command.MessageId))
            {
                return;
            }
        }

        _ = Task.Run(async () =>
        {
            try
            {
                await _runner.ExecuteAcceptedAsync(command, cancellationToken);
                _outbox.Drain();
            }
            catch (Exception error) when (!cancellationToken.IsCancellationRequested)
            {
                _log.LogError(error, "Durable command {MessageId} remains pending", command.MessageId);
            }
            finally
            {
                lock (_gate) { _running.Remove(command.MessageId); }
            }
        }, cancellationToken);
    }
}
