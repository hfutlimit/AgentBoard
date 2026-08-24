// SPDX-License-Identifier: MIT
namespace AgentBoard.Domain.Common;

/// <summary>
/// Base class for all domain entities. The primary key is typed as
/// <see cref="int"/> because every AgentBoard aggregate today uses
/// auto-incrementing integer ids; the project never adopted UUIDs.
/// If/when Guid-based entities are added, introduce <c>Entity&lt;TKey&gt;</c>.
/// </summary>
public abstract class Entity
{
    /// <summary>Primary key. Set by the persistence layer on insert.</summary>
    public int Id { get; protected set; }

    /// <summary>
    /// Optimistic-concurrency token. Updated by the database on every write
    /// (MySQL uses BIGINT auto-increment for the row_version column).
    /// </summary>
    public long RowVersion { get; protected set; }

    // Domain events raised by the entity, drained after SaveChanges.
    private readonly List<IDomainEvent> _domainEvents = new();

    /// <summary>Read-only view of pending domain events.</summary>
    public IReadOnlyCollection<IDomainEvent> DomainEvents => _domainEvents.AsReadOnly();

    protected void RaiseDomainEvent(IDomainEvent @event)
    {
        ArgumentNullException.ThrowIfNull(@event);
        _domainEvents.Add(@event);
    }

    /// <summary>Called by the dispatcher interceptor after persistence.</summary>
    public void ClearDomainEvents() => _domainEvents.Clear();
}
