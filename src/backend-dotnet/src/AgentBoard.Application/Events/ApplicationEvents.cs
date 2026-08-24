// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Events;

public interface IApplicationEvent
{
	DateTime OccurredAt { get; }
}

public interface IApplicationEventPublisher
{
	Task PublishAsync(IApplicationEvent @event, CancellationToken ct = default);
}

public interface IApplicationEventHandler<in TEvent> where TEvent : IApplicationEvent
{
	Task HandleAsync(TEvent @event, CancellationToken ct = default);
}

public sealed record ProjectCreatedEvent(
	int ProjectId,
	string Name,
	int? CreatedBy,
	DateTime OccurredAt) : IApplicationEvent;

public sealed record ProjectDeletedEvent(
	int ProjectId,
	DateTime OccurredAt) : IApplicationEvent;

public sealed record TaskUpdatedEvent(
	int TaskId,
	int ProjectId,
	string Status,
	DateTime OccurredAt) : IApplicationEvent;
