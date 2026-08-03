using RabbitMQ.Client;
using RabbitMQ.Client.Events;
using Microsoft.Extensions.Options;

namespace AgentBoard.ProposalWorker;

public sealed class RabbitMqConsumerService : BackgroundService
{
    private readonly RabbitMqOptions _mq; private readonly WorkerOptions _worker; private readonly ProposalExecutionService _execution; private readonly WorkerState _state; private readonly ILogger<RabbitMqConsumerService> _log;
    public RabbitMqConsumerService(IOptions<RabbitMqOptions> mq, IOptions<WorkerOptions> worker, ProposalExecutionService execution, WorkerState state, ILogger<RabbitMqConsumerService> log) => (_mq, _worker, _execution, _state, _log) = (mq.Value, worker.Value, execution, state, log);

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        if (string.IsNullOrWhiteSpace(_mq.Uri)) { _log.LogError("RabbitMq:Uri is required; consumer is disabled"); return; }
        while (!stoppingToken.IsCancellationRequested)
        {
            try { await ConsumeUntilDisconnected(stoppingToken); }
            catch (Exception ex) when (!stoppingToken.IsCancellationRequested) { _state.LastError = ex.Message; _log.LogError(ex, "RabbitMQ disconnected; retrying in 5 seconds"); await Task.Delay(TimeSpan.FromSeconds(5), stoppingToken); }
        }
    }

    private async Task ConsumeUntilDisconnected(CancellationToken ct)
    {
        var factory = new ConnectionFactory { Uri = new Uri(_mq.Uri), DispatchConsumersAsync = true, AutomaticRecoveryEnabled = true, NetworkRecoveryInterval = TimeSpan.FromSeconds(5) };
        using var connection = factory.CreateConnection(); using var channel = connection.CreateModel();
        channel.ExchangeDeclare(_mq.Namespace, ExchangeType.Direct, durable: true);
        channel.ExchangeDeclare(_mq.Namespace + ".dlx", ExchangeType.Direct, durable: true);
        channel.QueueDeclare(_mq.Namespace + ".dead", durable: true, exclusive: false, autoDelete: false);
        channel.QueueBind(_mq.Namespace + ".dead", _mq.Namespace + ".dlx", "dead");
        var dlqArguments = new Dictionary<string, object> { ["x-dead-letter-exchange"] = _mq.Namespace + ".dlx", ["x-dead-letter-routing-key"] = "dead" };
        channel.QueueDeclare(_mq.PublicQueue, durable: true, exclusive: false, autoDelete: false, arguments: dlqArguments);
        channel.QueueBind(_mq.PublicQueue, _mq.Namespace, _mq.PublicRoutingKey);
        channel.ExchangeDeclare(_mq.DirectExchange, ExchangeType.Direct, durable: true);
        var directQueue = _mq.WorkerQueue(_worker.Id);
        channel.QueueDeclare(directQueue, durable: true, exclusive: false, autoDelete: false, arguments: dlqArguments);
        channel.QueueBind(directQueue, _mq.DirectExchange, _mq.WorkerRoutingKey(_worker.Id));
        channel.BasicQos(0, Math.Max((ushort)1, _mq.Prefetch), false);
        var done = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        connection.ConnectionShutdown += (_, _) => done.TrySetResult();
        var publicTag = Consume(channel, _mq.PublicQueue, "public", ct);
        var directTag = Consume(channel, directQueue, "direct", ct);
        using var registration = ct.Register(() => done.TrySetResult());
        await done.Task;
        try { channel.BasicCancel(publicTag); channel.BasicCancel(directTag); } catch { }
    }

    private string Consume(IModel channel, string queue, string source, CancellationToken stoppingToken)
    {
        var consumer = new AsyncEventingBasicConsumer(channel);
        consumer.Received += async (_, eventArgs) =>
        {
            try
            {
                var message = ProposalMessage.Parse(eventArgs.Body);
                if (_state.Paused) { await Task.Delay(TimeSpan.FromSeconds(1), stoppingToken); channel.BasicNack(eventArgs.DeliveryTag, false, true); return; }
                var succeeded = await _execution.ExecuteAsync(message, source, stoppingToken);
                if (succeeded) channel.BasicAck(eventArgs.DeliveryTag, false);
                else channel.BasicNack(eventArgs.DeliveryTag, false, false);
            }
            catch (InvalidDataException ex) { _log.LogWarning(ex, "Poison proposal message on {Queue}", queue); channel.BasicNack(eventArgs.DeliveryTag, false, false); }
            catch (Exception ex) { _state.LastError = ex.Message; _log.LogError(ex, "Unhandled proposal execution failure"); channel.BasicNack(eventArgs.DeliveryTag, false, false); }
        };
        return channel.BasicConsume(queue, autoAck: false, consumer);
    }
}
