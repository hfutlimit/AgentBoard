// SPDX-License-Identifier: MIT
using System.Text;
using System.Text.Json;
using AgentBoard.Contracts;
using AgentBoard.Domain.Workflow.Durable;
using RabbitMQ.Client;

namespace AgentBoard.Infrastructure.Messaging;

/// <summary>RabbitMQ publisher-confirm transport for Server command outbox rows.</summary>
public sealed class DurableRabbitCommandTransport : ICommandTransport
{
    private readonly Uri _brokerUri;
    private readonly TimeSpan _confirmTimeout;

    public DurableRabbitCommandTransport(string brokerUri, TimeSpan? confirmTimeout = null)
    {
        if (!Uri.TryCreate(brokerUri, UriKind.Absolute, out var uri))
        {
            throw new ArgumentException("a valid RabbitMQ URI is required", nameof(brokerUri));
        }

        _brokerUri = uri;
        _confirmTimeout = confirmTimeout ?? TimeSpan.FromSeconds(5);
    }

    public PublishResult Publish(OutboxMessage message)
    {
        var command = JsonSerializer.Deserialize<CommandEnvelope>(message.Payload, ContractJson.Options)
            ?? throw new InvalidDataException("outbox payload is not a command envelope");
        var errors = EnvelopeValidator.Validate(command);
        if (errors.Count > 0)
        {
            throw new InvalidDataException("outbox command failed schema validation");
        }

        var factory = new ConnectionFactory
        {
            Uri = _brokerUri,
            AutomaticRecoveryEnabled = false,
        };
        using var connection = factory.CreateConnection();
        using var channel = connection.CreateModel();
        DeclareTopology(channel);
        DeclareWorkerQueue(channel, command.WorkerId);
        channel.ConfirmSelect();

        var properties = channel.CreateBasicProperties();
        properties.Persistent = true;
        properties.ContentType = "application/json";
        properties.Type = command.MessageType;
        properties.MessageId = command.MessageId;
        properties.CorrelationId = command.CorrelationId;
        properties.Headers = new Dictionary<string, object>
        {
            ["schema_version"] = Encoding.UTF8.GetBytes(command.SchemaVersion),
            ["traceparent"] = Encoding.UTF8.GetBytes(command.Traceparent!),
        };

        channel.BasicPublish(
            DurableMessaging.CommandExchange,
            DurableMessaging.WorkerRoutingKey(command.WorkerId),
            mandatory: true,
            basicProperties: properties,
            body: Encoding.UTF8.GetBytes(JsonSerializer.Serialize(command, ContractJson.Options)));
        return channel.WaitForConfirms(_confirmTimeout)
            ? PublishResult.Confirmed
            : PublishResult.Failed;
    }

    public static void DeclareTopology(IModel channel)
    {
        channel.ExchangeDeclare(DurableMessaging.CommandExchange, ExchangeType.Direct, durable: true);
        channel.ExchangeDeclare(DurableMessaging.ResultExchange, ExchangeType.Direct, durable: true);
        channel.ExchangeDeclare(DurableMessaging.DeadLetterExchange, ExchangeType.Direct, durable: true);
        channel.QueueDeclare(DurableMessaging.ServerResultQueue, durable: true, exclusive: false, autoDelete: false,
            arguments: new Dictionary<string, object>
            {
                ["x-dead-letter-exchange"] = DurableMessaging.DeadLetterExchange,
                ["x-dead-letter-routing-key"] = DurableMessaging.DeadLetterRoutingKey,
            });
        channel.QueueBind(DurableMessaging.ServerResultQueue,
            DurableMessaging.ResultExchange, DurableMessaging.ResultRoutingKey);
        channel.QueueDeclare(DurableMessaging.DeadLetterQueue, durable: true, exclusive: false, autoDelete: false);
        channel.QueueBind(DurableMessaging.DeadLetterQueue,
            DurableMessaging.DeadLetterExchange, DurableMessaging.DeadLetterRoutingKey);
    }

    public static void DeclareWorkerQueue(IModel channel, string workerId)
    {
        var queue = DurableMessaging.WorkerCommandQueue(workerId);
        channel.QueueDeclare(queue, durable: true, exclusive: false, autoDelete: false,
            arguments: new Dictionary<string, object>
            {
                ["x-dead-letter-exchange"] = DurableMessaging.DeadLetterExchange,
                ["x-dead-letter-routing-key"] = DurableMessaging.DeadLetterRoutingKey,
            });
        channel.QueueBind(queue, DurableMessaging.CommandExchange,
            DurableMessaging.WorkerRoutingKey(workerId));
    }
}
