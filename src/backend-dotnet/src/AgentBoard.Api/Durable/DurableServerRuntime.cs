// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Workflow.Durable;
using AgentBoard.Infrastructure.Persistence.Workflow;
using Microsoft.Extensions.Options;

namespace AgentBoard.Api.Durable;

public sealed class DurableWorkflowOptions
{
    public bool Enabled { get; set; }
    public string DatabasePath { get; set; } = "data/durable-workflow.db";
    public string RabbitMqUri { get; set; } = "";
    public ushort Prefetch { get; set; } = 8;
}

/// <summary>Serialized mutation and SQLite commit boundary for the Server plane.</summary>
public sealed class DurableServerRuntime : IDisposable
{
    private readonly object _gate = new();
    private readonly SqlitePlaneStore _store;
    private readonly DurableServerPlane _plane;

    public DurableServerRuntime(IOptions<DurableWorkflowOptions> options, IAgentSelector agentSelector)
    {
        var path = Path.GetFullPath(options.Value.DatabasePath);
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        _store = new SqlitePlaneStore(path);
        var state = _store.Load();
        _plane = state is null
            ? new DurableServerPlane(
                () => DateTimeOffset.UtcNow, () => Guid.NewGuid().ToString("N"), agentSelector: agentSelector)
            : DurableServerPlane.Restore(
                () => DateTimeOffset.UtcNow, () => Guid.NewGuid().ToString("N"), state, agentSelector);
    }

    public T Mutate<T>(Func<DurableServerPlane, T> work)
    {
        lock (_gate)
        {
            T value = default!;
            _plane.CommitAtomic(_store, () => value = work(_plane));
            return value;
        }
    }

    public void Mutate(Action<DurableServerPlane> work) =>
        Mutate(plane => { work(plane); return true; });

    public T Read<T>(Func<DurableServerPlane, T> read)
    {
        lock (_gate) { return read(_plane); }
    }

    /// <summary>
    /// Claims due outbox rows and commits that claim. RabbitMQ publication is
    /// intentionally performed by the caller after this method releases the
    /// global mutation lock.
    /// </summary>
    public IReadOnlyList<OutboxMessage> PrepareOutboxDispatches(int maximum = 32) =>
        Mutate(plane =>
        {
            plane.ExpireApprovals();
            plane.ProcessDueRetries();
            plane.Orchestrator.ResumePendingAssignments();
            return plane.Outbox.BeginDueDispatches(
                DateTimeOffset.UtcNow, maximum, TimeSpan.FromMinutes(1));
        });

    public OutboxState CompleteOutboxDispatch(
        OutboxMessage attempted,
        PublishResult outcome,
        string? publishError = null) =>
        Mutate(plane => plane.Outbox.CompleteDispatch(
            attempted,
            outcome,
            DateTimeOffset.UtcNow,
            plane.Planner,
            plane.DeadLetters,
            publishError));

    public void Dispose() => _store.Dispose();
}
