// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Infrastructure.Persistence;
using AgentBoard.Infrastructure.Persistence.Interceptors;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Diagnostics;
using Microsoft.Extensions.Logging.Abstractions;
using NSubstitute;

namespace AgentBoard.Infrastructure.Tests;

/// <summary>
/// In-memory DbContext factory for unit tests. Registers the three
/// SaveChanges interceptors (Audit / SoftDelete / DomainEvent) so the
/// behaviour under test matches production wiring.
/// </summary>
public static class TestDbContextFactory
{
    public static AppDbContext Create(
        IClock? clock = null,
        ICurrentUser? current = null,
        string dbName = "test-db")
    {
        clock ??= new FrozenClock(new DateTime(2026, 1, 1, 0, 0, 0, DateTimeKind.Utc));
        current ??= Substitute.For<ICurrentUser>();

        var options = new DbContextOptionsBuilder<AppDbContext>()
            .UseInMemoryDatabase(dbName)
            .EnableServiceProviderCaching(false)
            .ConfigureWarnings(w => w.Ignore(InMemoryEventId.TransactionIgnoredWarning))
            .Options;

        return new AppDbContext(options);
    }

    public static (AppDbContext db, AuditFieldsInterceptor audit, SoftDeleteInterceptor soft,
        DomainEventDispatcherInterceptor events, FrozenClock clock) CreateWithInterceptors(
        string dbName = "test-db-interceptors")
    {
        var clock = new FrozenClock(new DateTime(2026, 1, 1, 0, 0, 0, DateTimeKind.Utc));
        var current = Substitute.For<ICurrentUser>();
        current.UserId.Returns(42);
        current.IsAdmin.Returns(false);

        var audit = new AuditFieldsInterceptor(clock, current);
        var soft = new SoftDeleteInterceptor(clock, current);
        // Empty service provider — tests don't register IDomainEventHandler<>
        // implementations, so the dispatcher is a no-op in these tests.
        var dispatcher = new DomainEventDispatcherInterceptor(
            EmptyServiceProvider.Instance,
            NullLogger<DomainEventDispatcherInterceptor>.Instance);

        var options = new DbContextOptionsBuilder<AppDbContext>()
            .UseInMemoryDatabase(dbName)
            .EnableServiceProviderCaching(false)
            .AddInterceptors(audit, soft, dispatcher)
            .Options;

        var db = new AppDbContext(options);
        return (db, audit, soft, dispatcher, clock);
    }

    private sealed class EmptyServiceProvider : IServiceProvider
    {
        public static readonly EmptyServiceProvider Instance = new();
        public object? GetService(Type serviceType)
        {
            // Return an empty array for any IEnumerable<T> so callers using
            // GetServices(IEnumerable<T>) get an empty list back instead of
            // throwing "service not registered".
            if (serviceType.IsGenericType
                && serviceType.GetGenericTypeDefinition() == typeof(IEnumerable<>))
            {
                return Array.CreateInstance(serviceType.GetGenericArguments()[0], 0);
            }
            return null;
        }
    }
}

/// <summary>Deterministic clock for tests; tick with <see cref="Advance"/>.</summary>
public sealed class FrozenClock : IClock
{
    public FrozenClock(DateTime initialUtc) { UtcNow = initialUtc; }
    public DateTime UtcNow { get; private set; }
    public void Advance(TimeSpan by) => UtcNow = UtcNow.Add(by);
}
