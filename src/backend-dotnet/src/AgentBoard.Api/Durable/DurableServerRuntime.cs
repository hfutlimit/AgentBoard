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

    public DurableServerRuntime(IOptions<DurableWorkflowOptions> options)
    {
        var path = Path.GetFullPath(options.Value.DatabasePath);
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        _store = new SqlitePlaneStore(path);
        var state = _store.Load();
        _plane = state is null
            ? new DurableServerPlane(() => DateTimeOffset.UtcNow, () => Guid.NewGuid().ToString("N"))
            : DurableServerPlane.Restore(
                () => DateTimeOffset.UtcNow, () => Guid.NewGuid().ToString("N"), state);
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

    public void Dispose() => _store.Dispose();
}
