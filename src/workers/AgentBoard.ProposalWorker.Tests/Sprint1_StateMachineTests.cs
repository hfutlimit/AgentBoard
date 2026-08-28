using AgentBoard.ProposalWorker;
using AgentBoard.ProposalWorker.Agents;
using AgentBoard.ProposalWorker.Execution;
using AgentBoard.ProposalWorker.Tests.Fixtures;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Xunit;

namespace AgentBoard.ProposalWorker.Tests;

/// <summary>
/// Sprint 1: Execution State Machine self-verification.
/// Per-test TempDb → no cross-test UNIQUE conflicts.
/// </summary>
public sealed class Sprint1_StateMachineTests : IDisposable
{
    private readonly TempDbFixture _fx = new();
    private long _nextId = 1000;  // unique per test

    public void Dispose() => _fx.Dispose();

    private ExecutionRequest Req(string agent = "test") => new(
        ExecutionKey: $"proposal:{_nextId++}:0:{agent}",
        WorkloadType: "proposal",
        WorkloadId: _nextId - 1,
        AgentType: agent,
        Round: 0,
        Source: "test",
        PayloadJson: "{}");

    // -------------------------------------------------------------------------
    // Orphan recovery — kill -9 self-prove
    // -------------------------------------------------------------------------

    [Fact]
    public async Task MarkOrphansAsync_marks_old_running_execution_as_timed_out()
    {
        var id = await _fx.Store.StartAsync(Req(), "test", CancellationToken.None);
        BackdateStartedAt(id, TimeSpan.FromMinutes(60));

        var n = await _fx.Store.MarkOrphansAsync(thresholdMinutes: 30, CancellationToken.None);
        Assert.Equal(1, n);

        var rec = await _fx.Store.GetAsync(id, CancellationToken.None);
        Assert.NotNull(rec);
        Assert.Equal("TimedOut", rec!.Status);
        Assert.Equal("orphaned", rec.FailureReason);
        Assert.NotNull(rec.ErrorStack);
    }

    [Fact]
    public async Task MarkOrphansAsync_does_not_touch_recent_running_executions()
    {
        var stale = await _fx.Store.StartAsync(Req(), "test", CancellationToken.None);
        var fresh = await _fx.Store.StartAsync(Req(), "test", CancellationToken.None);
        BackdateStartedAt(stale, TimeSpan.FromMinutes(60));

        await _fx.Store.MarkOrphansAsync(thresholdMinutes: 30, CancellationToken.None);

        Assert.Equal("TimedOut", (await _fx.Store.GetAsync(stale, CancellationToken.None))!.Status);
        Assert.Equal("Running", (await _fx.Store.GetAsync(fresh, CancellationToken.None))!.Status);
    }

    private void BackdateStartedAt(long id, TimeSpan age)
    {
        using var conn = new Microsoft.Data.Sqlite.SqliteConnection($"Data Source={_fx.DatabasePath}");
        conn.Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "UPDATE executions SET started_at=$at WHERE id=$id";
        cmd.Parameters.AddWithValue("$at", DateTimeOffset.UtcNow.Subtract(age).ToString("O"));
        cmd.Parameters.AddWithValue("$id", id);
        cmd.ExecuteNonQuery();
    }

    // -------------------------------------------------------------------------
    // Four terminal branches — adapter return shape → expected status
    // -------------------------------------------------------------------------

    [Theory]
    [InlineData("Succeeded", "Success", null)]
    [InlineData("Failed",    "Failure", "boom")]
    [InlineData("TimedOut",  "TimedOut", "timeout")]
    [InlineData("Cancelled", "Cancelled", "cancelled")]
    public async Task Terminal_branch_writes_correct_status(string expectedStatus, string adapterKind, string? expectedError)
    {
        IAgentAdapter adapter = adapterKind switch
        {
            "Success"   => FakeAgentAdapter.Success("test"),
            "Failure"   => FakeAgentAdapter.Failure("test", "boom"),
            "TimedOut"  => FakeAgentAdapter.TimedOut("test"),
            "Cancelled" => FakeAgentAdapter.Cancelled("test"),
            _ => throw new InvalidOperationException()
        };
        var coord = MakeCoordinator(adapter);
        var req = Req();
        var (inboxId, _) = await _fx.Inbox.TryEnqueueAsync(req, CancellationToken.None);
        await coord.ExecuteAsync(req, inboxId, CancellationToken.None);

        var executionId = FindExecutionId(req.ExecutionKey);
        var rec = await _fx.Store.GetAsync(executionId, CancellationToken.None);
        Assert.Equal(expectedStatus, rec!.Status);
        if (expectedError is not null) Assert.Equal(expectedError, rec.Error);
    }

    [Fact]
    public async Task Succeeded_path_writes_exit_code_zero()
    {
        var coord = MakeCoordinator(FakeAgentAdapter.Success("test"));
        var req = Req();
        var (inboxId, _) = await _fx.Inbox.TryEnqueueAsync(req, CancellationToken.None);
        await coord.ExecuteAsync(req, inboxId, CancellationToken.None);

        var rec = await _fx.Store.GetAsync(FindExecutionId(req.ExecutionKey), CancellationToken.None);
        Assert.Equal("Succeeded", rec!.Status);
        Assert.Equal(0, rec.ExitCode);
    }

    // -------------------------------------------------------------------------
    // CAS write — only one terminal write wins
    // -------------------------------------------------------------------------

    [Fact]
    public async Task Concurrent_terminal_writes_only_one_succeeds()
    {
        var id = await _fx.Store.StartAsync(Req(), "test", CancellationToken.None);

        var t1 = _fx.Store.MarkSucceededAsync(id, 0, "ok", CancellationToken.None);
        var t2 = _fx.Store.MarkFailedAsync(id, 1, "", "raced", null, CancellationToken.None);
        var results = await Task.WhenAll(t1, t2);

        Assert.Equal(1, results.Count(r => r));
        Assert.Equal(1, results.Count(r => !r));

        var rec = await _fx.Store.GetAsync(id, CancellationToken.None);
        Assert.True(rec!.Status is "Succeeded" or "Failed");
    }

    // -------------------------------------------------------------------------
    // Helpers
    // -------------------------------------------------------------------------

    private ExecutionCoordinator MakeCoordinator(IAgentAdapter adapter)
    {
        var registry = new AgentAdapterRegistry(new[] { adapter }, NullLogger<AgentAdapterRegistry>.Instance);
        var state = new WorkerState(Options.Create(_fx.Options), new WorkerIdentity(Options.Create(_fx.Options)));
        return new ExecutionCoordinator(
            _fx.Store, _fx.Inbox, registry, state,
            NullLogger<ExecutionCoordinator>.Instance);
    }

    private long FindExecutionId(string executionKey)
    {
        using var conn = new Microsoft.Data.Sqlite.SqliteConnection($"Data Source={_fx.DatabasePath}");
        conn.Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "SELECT id FROM executions WHERE execution_key=$k";
        cmd.Parameters.AddWithValue("$k", executionKey);
        return (long)(cmd.ExecuteScalar() ?? 0L);
    }
}
