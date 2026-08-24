// SPDX-License-Identifier: MIT
using AgentBoard.Application.Events;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;

namespace AgentBoard.Infrastructure.Events;

/// <summary>
/// Dispatches application events to in-process handlers. A durable outbox can
/// replace this implementation without changing application services or API
/// controllers.
/// </summary>
public sealed class ApplicationEventPublisher : IApplicationEventPublisher
{
	private readonly IServiceProvider _services;
	private readonly ILogger<ApplicationEventPublisher> _logger;

	public ApplicationEventPublisher(
		IServiceProvider services,
		ILogger<ApplicationEventPublisher> logger)
	{
		_services = services ?? throw new ArgumentNullException(nameof(services));
		_logger = logger ?? throw new ArgumentNullException(nameof(logger));
	}

	public async Task PublishAsync(IApplicationEvent @event, CancellationToken ct = default)
	{
		ArgumentNullException.ThrowIfNull(@event);
		var handlerType = typeof(IApplicationEventHandler<>).MakeGenericType(@event.GetType());
		foreach (var handler in _services.GetServices(handlerType))
		{
			try
			{
				var method = handlerType.GetMethod(nameof(IApplicationEventHandler<IApplicationEvent>.HandleAsync));
				if (method is null) continue;
				await (Task)method.Invoke(handler, new object?[] { @event, ct })!;
			}
			catch (Exception ex)
			{
				_logger.LogError(ex, "Application event handler {Handler} failed for {Event}",
					handler?.GetType().FullName, @event.GetType().FullName);
			}
		}
	}
}
