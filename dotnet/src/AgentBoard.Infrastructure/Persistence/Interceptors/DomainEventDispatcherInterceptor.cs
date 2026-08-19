// SPDX-License-Identifier: MIT
using System.Collections.Concurrent;
using AgentBoard.Application.Abstractions;
using AgentBoard.Domain.Common;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Diagnostics;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;

namespace AgentBoard.Infrastructure.Persistence.Interceptors;

/// <summary>
/// Drains <see cref="Entity.DomainEvents"/> after a successful SaveChanges
/// and dispatches them to <c>IDomainEventHandler&lt;T&gt;</c> implementations
/// resolved from the request-scoped service provider.
///
/// Handlers are awaited in registration order; if a handler throws the error
/// is logged but does not roll back the transaction (the data is already
/// persisted). For "must-succeed" semantics wrap the handler invocation in
/// an outbox table — that lands in stage 1 alongside the MQ work.
/// </summary>
public sealed class DomainEventDispatcherInterceptor : SaveChangesInterceptor
{
    private static readonly ConcurrentDictionary<Type, Type> HandlerTypeCache = new();

    private readonly IServiceProvider _services;
    private readonly ILogger<DomainEventDispatcherInterceptor> _logger;

    public DomainEventDispatcherInterceptor(
        IServiceProvider services,
        ILogger<DomainEventDispatcherInterceptor> logger)
    {
        _services = services ?? throw new ArgumentNullException(nameof(services));
        _logger = logger ?? throw new ArgumentNullException(nameof(logger));
    }

    public override async ValueTask<InterceptionResult<int>> SavingChangesAsync(
        DbContextEventData eventData, InterceptionResult<int> result, CancellationToken ct = default)
    {
        var db = eventData.Context;
        if (db is null) return await base.SavingChangesAsync(eventData, result, ct);

        // Pre-collect events so we can dispatch after commit.
        var pending = db.ChangeTracker
            .Entries<Entity>()
            .SelectMany(e => e.Entity.DomainEvents)
            .ToList();
        if (pending.Count == 0)
            return await base.SavingChangesAsync(eventData, result, ct);

        var result1 = await base.SavingChangesAsync(eventData, result, ct);
        await DispatchAsync(pending, ct);
        ClearEvents(db);
        return result1;
    }

    public override async Task SaveChangesFailedAsync(
        DbContextErrorEventData eventData, CancellationToken ct = default)
    {
        // Nothing to clean up — events stay in the entities' DomainEvents list.
        await base.SaveChangesFailedAsync(eventData, ct);
    }

    private async Task DispatchAsync(IReadOnlyList<IDomainEvent> events, CancellationToken ct)
    {
        // Resolve handlers from the request-scoped service provider passed in
        // by the DI configuration. The Provider is expected to be the per-request
        // scope created by ASP.NET Core; we don't create a nested scope here so
        // unit tests can pass a plain EmptyServiceProvider without registering
        // IServiceScopeFactory.
        foreach (var @event in events)
        {
            var handlerType = HandlerTypeCache.GetOrAdd(
                @event.GetType(),
                t => typeof(IDomainEventHandler<>).MakeGenericType(t));
            var handlers = _services.GetServices(handlerType);
            foreach (var handler in handlers)
            {
                try
                {
                    var method = handlerType.GetMethod(nameof(IDomainEventHandler<IDomainEvent>.HandleAsync))!;
                    var task = (Task)method.Invoke(handler, new object?[] { @event, ct })!;
                    await task;
                }
                catch (Exception ex)
                {
                    _logger.LogError(ex,
                        "Domain event handler {Handler} failed for event {Event}",
                        handler?.GetType().FullName, @event.GetType().FullName);
                }
            }
        }
    }

    private static void ClearEvents(DbContext db)
    {
        foreach (var entry in db.ChangeTracker.Entries<Entity>())
            entry.Entity.ClearDomainEvents();
    }
}

/// <summary>Handler contract for a single domain event type.</summary>
public interface IDomainEventHandler<in TEvent> where TEvent : IDomainEvent
{
    Task HandleAsync(TEvent @event, CancellationToken ct);
}
