using System.Threading.Channels;
using AgentBoard.ProposalWorker;
using AgentBoard.ProposalWorker.Agents;
using AgentBoard.ProposalWorker.Execution;
using AgentBoard.ProposalWorker.Tests.Fixtures;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Xunit;

namespace AgentBoard.ProposalWorker.Tests;

public sealed class Sprint3_DecouplingTests
{
    private static ExecutionRequest Req(string agent = "workbuddy", long id = 1) => new(
        ExecutionKey: $"proposal:{id}:0:{agent}",
        WorkloadType: "proposal",
        WorkloadId: id,
        AgentType: agent,
        Round: 0,
        Source: "test",
        PayloadJson: "{}");

    // -------------------------------------------------------------------------
    // Bounded channel: writes block when capacity is full and no reader drains.
    // -------------------------------------------------------------------------

    [Fact]
    public async Task Bounded_channel_blocks_writer_when_full_and_no_reader()
    {
        var opts = Options.Create(new WorkerOptions { DispatchChannelCapacity = 2 });
        var channel = new ExecutionChannel(opts);

        // First two writes complete immediately.
        await channel.Writer.WriteAsync(new InFlightExecution(Req(id: 1), 1), CancellationToken.None);
        await channel.Writer.WriteAsync(new InFlightExecution(Req(id: 2), 2), CancellationToken.None);

        // Third write must block (capacity=2, no reader).
        using var cts = new CancellationTokenSource(TimeSpan.FromMilliseconds(100));
        await Assert.ThrowsAnyAsync<OperationCanceledException>(async () =>
            await channel.Writer.WriteAsync(new InFlightExecution(Req(id: 3), 3), cts.Token));
    }

    [Fact]
    public async Task Bounded_channel_drains_when_reader_consumes()
    {
        var opts = Options.Create(new WorkerOptions { DispatchChannelCapacity = 1 });
        var channel = new ExecutionChannel(opts);

        // Write 1 item that fits.
        await channel.Writer.WriteAsync(new InFlightExecution(Req(id: 1), 1), CancellationToken.None);

        // Start a reader that consumes immediately.
        var first = await channel.Reader.ReadAsync(CancellationToken.None);
        Assert.Equal(1L, first.InboxId);

        // Now writer can complete again because reader drained.
        await channel.Writer.WriteAsync(new InFlightExecution(Req(id: 2), 2), CancellationToken.None);
        var second = await channel.Reader.ReadAsync(CancellationToken.None);
        Assert.Equal(2L, second.InboxId);
    }

    // -------------------------------------------------------------------------
    // Dispatcher isolation: one execution throwing does not kill the channel.
    // -------------------------------------------------------------------------

    [Fact]
    public async Task Dispatcher_continues_after_an_execution_throws()
    {
        // We test this at the coordinator level (no hosted service) because
        // the dispatcher's only job is to call coordinator + catch errors.
        var opts = Options.Create(new WorkerOptions { HistoryDatabasePath = Path.Combine(Path.GetTempPath(), $"dtest-{Guid.NewGuid():N}.db") });
        var store = new ExecutionStore(opts, NullLogger<ExecutionStore>.Instance);
        var inbox = new InboxStore(store, NullLogger<InboxStore>.Instance);
        var registry = new AgentAdapterRegistry(new[]
        {
            FakeAgentAdapter.Throws("workbuddy", new InvalidOperationException("kaboom")),
            FakeAgentAdapter.Success("minimax"),
        }, NullLogger<AgentAdapterRegistry>.Instance);
        var state = new WorkerState(opts, new WorkerIdentity(opts));
        var coord = new ExecutionCoordinator(store, inbox, registry, state, NullLogger<ExecutionCoordinator>.Instance);

        // 1) throwing execution
        var r1 = Req(agent: "workbuddy", id: 1);
        var (inboxId1, _) = await inbox.TryEnqueueAsync(r1, CancellationToken.None);
        await coord.ExecuteAsync(r1, inboxId1, CancellationToken.None);

        // 2) succeeding execution right after
        var r2 = Req(agent: "minimax", id: 2);
        var (inboxId2, _) = await inbox.TryEnqueueAsync(r2, CancellationToken.None);
        await coord.ExecuteAsync(r2, inboxId2, CancellationToken.None);

        // The second one must have completed even though the first threw.
        var recs = await store.ListAsync(10);
        var minimaxRec = recs.First(r => r.AgentType == "minimax");
        Assert.Equal("Succeeded", minimaxRec.Status);

        // Cleanup
        try { File.Delete(opts.Value.HistoryDatabasePath); } catch { }
    }

    // -------------------------------------------------------------------------
    // InflightExecution record carries both request and inbox id
    // -------------------------------------------------------------------------

    [Fact]
    public void InFlightExecution_carries_request_and_inbox_id()
    {
        var req = Req(id: 99);
        var flight = new InFlightExecution(req, 42L);
        Assert.Equal("proposal:99:0:workbuddy", flight.Request.ExecutionKey);
        Assert.Equal(42L, flight.InboxId);
    }
}
