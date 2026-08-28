using AgentBoard.ProposalWorker;
using AgentBoard.ProposalWorker.Execution;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;

namespace AgentBoard.ProposalWorker.Tests.Fixtures;

/// <summary>
/// Per-test SQLite temp database. Each fixture instance owns one .db file
/// under %TEMP% and deletes it on dispose. Both ExecutionStore and InboxStore
/// share the same connection string so they coexist in one DB.
///
/// IMPORTANT: do NOT share this across tests via xUnit Collection. Each
/// test class creates its own in the constructor and disposes in Dispose,
/// so each test gets a fresh DB — the UNIQUE(execution_key) constraint
/// doesn't conflict with prior tests.
/// </summary>
public sealed class TempDbFixture : IDisposable
{
    public string DatabasePath { get; }
    public WorkerOptions Options { get; }
    public ExecutionStore Store { get; }
    public InboxStore Inbox { get; }

    public TempDbFixture()
    {
        DatabasePath = Path.Combine(Path.GetTempPath(), $"worker-test-{Guid.NewGuid():N}.db");
        Options = new WorkerOptions
        {
            Id = "test-worker",
            HistoryDatabasePath = DatabasePath,
            OrphanThresholdMinutes = 30,
        };
        Store = new ExecutionStore(Microsoft.Extensions.Options.Options.Create(Options), NullLogger<ExecutionStore>.Instance);
        Inbox = new InboxStore(Store, NullLogger<InboxStore>.Instance);
    }

    public void Dispose()
    {
        GC.SuppressFinalize(this);
        try { if (File.Exists(DatabasePath)) File.Delete(DatabasePath); } catch { /* best-effort */ }
    }
}
