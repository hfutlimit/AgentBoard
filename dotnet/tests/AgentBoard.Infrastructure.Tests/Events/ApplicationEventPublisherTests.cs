// SPDX-License-Identifier: MIT
using AgentBoard.Application.Events;
using AgentBoard.Infrastructure.Events;
using FluentAssertions;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;

namespace AgentBoard.Infrastructure.Tests.Events;

public sealed class ApplicationEventPublisherTests
{
	[Fact]
	public async Task Publishes_to_registered_handler_without_transport_dependency()
	{
		var services = new ServiceCollection();
		services.AddLogging();
		services.AddSingleton<CaptureHandler>();
		services.AddSingleton<IApplicationEventHandler<ProjectCreatedEvent>>(sp => sp.GetRequiredService<CaptureHandler>());
		await using var provider = services.BuildServiceProvider();
		var publisher = new ApplicationEventPublisher(
			provider,
			provider.GetRequiredService<ILogger<ApplicationEventPublisher>>());
		var @event = new ProjectCreatedEvent(7, "Events", 3, DateTime.UtcNow);

		await publisher.PublishAsync(@event);

		provider.GetRequiredService<CaptureHandler>().Received.Should().ContainSingle().Which.Should().Be(@event);
	}

	private sealed class CaptureHandler : IApplicationEventHandler<ProjectCreatedEvent>
	{
		public List<ProjectCreatedEvent> Received { get; } = new();

		public Task HandleAsync(ProjectCreatedEvent @event, CancellationToken ct = default)
		{
			Received.Add(@event);
			return Task.CompletedTask;
		}
	}
}
