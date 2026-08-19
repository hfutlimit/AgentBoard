// SPDX-License-Identifier: MIT
namespace AgentBoard.Domain.Common;

/// <summary>
/// Marker for domain events. The <c>DomainEventDispatcherInterceptor</c>
/// resolves all <c>IDomainEventHandler&lt;TEvent&gt;</c> implementations from
/// the DI container and invokes them in registration order after a successful
/// SaveChanges. Handlers may publish to RabbitMQ / SignalR / etc.
/// </summary>
public interface IDomainEvent
{
    DateTime OccurredAt { get; }
}
