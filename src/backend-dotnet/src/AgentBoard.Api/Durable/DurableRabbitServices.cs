// SPDX-License-Identifier: MIT
using System.Text.Json;
using AgentBoard.Contracts;
using AgentBoard.Domain.Workflow.Durable;
using AgentBoard.Infrastructure.Messaging;
using Microsoft.Extensions.Options;
using RabbitMQ.Client;
using RabbitMQ.Client.Events;

namespace AgentBoard.Api.Durable;

public sealed class DurableServerOutboxService : BackgroundService
{
    private readonly DurableWorkflowOptions _options;
    private readonly IServiceProvider _services;
    private readonly ILogger<DurableServerOutboxService> _log;

    public DurableServerOutboxService(
        IOptions<DurableWorkflowOptions> options,
        IServiceProvider services,
        ILogger<DurableServerOutboxService> log)
    {
        _options = options.Value;
        _services = services;
        _log = log;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        if (!_options.Enabled) return;
        if (!Uri.TryCreate(_options.RabbitMqUri, UriKind.Absolute, out _))
            throw new InvalidOperationException("DurableWorkflow:RabbitMqUri is required when enabled");

        var runtime = _services.GetRequiredService<DurableServerRuntime>();
        var transport = new DurableRabbitCommandTransport(_options.RabbitMqUri);
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(1));
        while (await timer.WaitForNextTickAsync(stoppingToken))
        {
            try
            {
                runtime.Mutate(plane =>
                {
                    plane.ExpireApprovals();
                    return new OutboxDispatcher(plane.Outbox, transport, plane.Planner,
                        plane.DeadLetters, () => DateTimeOffset.UtcNow).DispatchDue();
                });
            }
            catch (Exception error) when (!stoppingToken.IsCancellationRequested)
            {
                _log.LogError(error, "Durable Server outbox pass failed; rows remain queryable");
            }
        }
    }
}

public sealed class DurableServerResultConsumerService : BackgroundService
{
    private readonly DurableWorkflowOptions _options;
    private readonly IServiceProvider _services;
    private readonly ILogger<DurableServerResultConsumerService> _log;

    public DurableServerResultConsumerService(
        IOptions<DurableWorkflowOptions> options,
        IServiceProvider services,
        ILogger<DurableServerResultConsumerService> log)
    {
        _options = options.Value;
        _services = services;
        _log = log;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        if (!_options.Enabled) return;
        if (!Uri.TryCreate(_options.RabbitMqUri, UriKind.Absolute, out _))
            throw new InvalidOperationException("DurableWorkflow:RabbitMqUri is required when enabled");

        var runtime = _services.GetRequiredService<DurableServerRuntime>();
        while (!stoppingToken.IsCancellationRequested)
        {
            try { await Consume(runtime, stoppingToken); }
            catch (Exception error) when (!stoppingToken.IsCancellationRequested)
            {
                _log.LogError(error, "Durable result consumer disconnected; retrying");
                await Task.Delay(TimeSpan.FromSeconds(5), stoppingToken);
            }
        }
    }

    private async Task Consume(DurableServerRuntime runtime, CancellationToken cancellationToken)
    {
        var factory = new ConnectionFactory
        {
            Uri = new Uri(_options.RabbitMqUri),
            DispatchConsumersAsync = true,
            AutomaticRecoveryEnabled = true,
            NetworkRecoveryInterval = TimeSpan.FromSeconds(5),
        };
        using var connection = factory.CreateConnection();
        using var channel = connection.CreateModel();
        DurableRabbitCommandTransport.DeclareTopology(channel);
        channel.BasicQos(0, Math.Max((ushort)1, _options.Prefetch), global: false);

        var stopped = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        connection.ConnectionShutdown += (_, _) => stopped.TrySetResult();
        var consumer = new AsyncEventingBasicConsumer(channel);
        consumer.Received += async (_, delivery) =>
        {
            ResultEnvelope? result;
            try
            {
                result = JsonSerializer.Deserialize<ResultEnvelope>(delivery.Body.Span, ContractJson.Options);
            }
            catch (JsonException)
            {
                channel.BasicNack(delivery.DeliveryTag, multiple: false, requeue: false);
                return;
            }

            if (result is null)
            {
                channel.BasicNack(delivery.DeliveryTag, multiple: false, requeue: false);
                return;
            }

            try
            {
                var verdict = runtime.Mutate(plane => plane.Results.Process(result));
                channel.BasicAck(delivery.DeliveryTag, multiple: false);
                _log.LogInformation("Durable result {MessageId}: {Kind}", result.MessageId, verdict.Kind);
            }
            catch (Exception error)
            {
                // No ACK: the transaction rolled back, so redelivery is safe.
                _log.LogError(error, "Durable result {MessageId} was not committed", result.MessageId);
                channel.BasicNack(delivery.DeliveryTag, multiple: false, requeue: true);
            }

            await Task.CompletedTask;
        };

        var tag = channel.BasicConsume(DurableMessaging.ServerResultQueue, autoAck: false, consumer);
        using var registration = cancellationToken.Register(() => stopped.TrySetResult());
        await stopped.Task;
        try { channel.BasicCancel(tag); } catch { }
    }
}
