using System.Threading.Channels;
using AgentBoard.Node;
using AgentBoard.Node.Agents;
using AgentBoard.Node.Execution;
using AgentBoard.Node.Tests.Fixtures;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Xunit;

namespace AgentBoard.Node.Tests;

public sealed class Sprint3_DecouplingTests
{
    private static WakeSignal Wake(string source = "test") =>
        new() { At = DateTimeOffset.UtcNow, Source = source };

    // -------------------------------------------------------------------------
    // Bounded channel semantics: round-7 uses FullMode=DropWrite
    // (previously Wait). The channel carries only a WakeSignal
    // sentinel in the DB-first architecture, so dropping a wake is
    // harmless: the Dispatcher's periodic 2 s timer + DB poll will
    // pick up the work even if the wake is lost. The bounded
    // capacity still caps in-memory usage; the channel just no
    // longer blocks the producer. This keeps the RabbitMQ
    // consumer thread never-stuck on a slow Dispatcher.
    // -------------------------------------------------------------------------

    [Fact]
    public async Task Bounded_channel_drops_writer_when_full_and_no_reader()
    {
        // BoundedChannelFullMode.DropWrite semantics (verified against
        // the .NET 10 docs): when the channel is full, the WRITE
        // itself is dropped. The channel contents are unchanged —
        // the oldest/newest item is NOT removed. The write "succeeds"
        // (TryWrite returns true, WriteAsync returns immediately)
        // but the new item never enters the buffer. The point is
        // exactly that: the producer is never blocked, the
        // bounded buffer is a soft cap that the periodic DB poll
        // covers.
        var opts = Options.Create(new NodeOptions { DispatchChannelCapacity = 2 });
        var channel = new ExecutionChannel(opts);

        // First two writes fit.
        await channel.Writer.WriteAsync(Wake("w1"), CancellationToken.None);
        await channel.Writer.WriteAsync(Wake("w2"), CancellationToken.None);

        // Third write would normally block (Wait) but with DropWrite
        // returns immediately. TryWrite returns true (the write
        // "succeeded" — it was dropped). Critically, the call does
        // not block on consumer state.
        var sw = System.Diagnostics.Stopwatch.StartNew();
        var accepted = channel.Writer.TryWrite(Wake("w3"));
        sw.Stop();
        Assert.True(accepted);
        Assert.True(sw.Elapsed < TimeSpan.FromMilliseconds(100),
            $"TryWrite took {sw.Elapsed}; should be immediate on DropWrite");

        // The dropped write is NOT in the buffer; w1, w2 are still there.
        var buf = new List<WakeSignal>();
        while (channel.Reader.TryRead(out var s)) buf.Add(s);
        Assert.DoesNotContain(buf, s => s.Source == "w3");  // dropped
        Assert.Contains(buf, s => s.Source == "w1");
        Assert.Contains(buf, s => s.Source == "w2");
    }

    [Fact]
    public async Task Bounded_channel_drains_when_reader_consumes()
    {
        var opts = Options.Create(new NodeOptions { DispatchChannelCapacity = 1 });
        var channel = new ExecutionChannel(opts);

        // Write 1 item that fits.
        await channel.Writer.WriteAsync(Wake("w1"), CancellationToken.None);

        // Start a reader that consumes immediately.
        var first = await channel.Reader.ReadAsync(CancellationToken.None);
        Assert.Equal("w1", first.Source);

        // Now writer can complete again because reader drained the
        // single slot.
        await channel.Writer.WriteAsync(Wake("w2"), CancellationToken.None);
        var second = await channel.Reader.ReadAsync(CancellationToken.None);
        Assert.Equal("w2", second.Source);
    }

    [Fact]
    public async Task Bounded_channel_never_blocks_writer_regardless_of_consumer_state()
    {
        // The old FullMode=Wait design would deadlock the consumer
        // thread when the Dispatcher stopped reading wakes (paused
        // / degraded / dead). The new FullMode=DropWrite is
        // designed so WriteAsync never blocks. This test asserts
        // that contract directly: write 1000 wakes into a
        // capacity=1 channel with no reader. With Wait, this would
        // stall the test runner (the assertion would never be
        // reached). With DropWrite, every write returns
        // immediately and the test completes in milliseconds.
        var opts = Options.Create(new NodeOptions { DispatchChannelCapacity = 1 });
        var channel = new ExecutionChannel(opts);

        var sw = System.Diagnostics.Stopwatch.StartNew();
        for (int i = 0; i < 1000; i++)
        {
            // WriteAsync returns ValueTask; we don't care if the
            // oldest entry is dropped to make room — that's the
            // whole point of DropWrite. The invariant under test is
            // "does not block".
            await channel.Writer.WriteAsync(Wake($"w{i}"), CancellationToken.None);
        }
        sw.Stop();

        Assert.True(sw.Elapsed < TimeSpan.FromSeconds(2),
            $"1000 writes took {sw.Elapsed}; with Wait they would stall forever, with DropWrite should be near-instant");
    }

    // -------------------------------------------------------------------------
    // Dispatcher isolation: one execution throwing does not kill the channel.
    // -------------------------------------------------------------------------

    [Fact]
    public async Task Dispatcher_continues_after_an_execution_throws()
    {
        // We test this at the coordinator level (no hosted service) because
        // the dispatcher's only job is to call coordinator + catch errors.
        var opts = Options.Create(new NodeOptions { HistoryDatabasePath = Path.Combine(Path.GetTempPath(), $"dtest-{Guid.NewGuid():N}.db") });
        var store = new ExecutionStore(opts, NullLogger<ExecutionStore>.Instance);
        var inbox = new InboxStore(store, NullLogger<InboxStore>.Instance);
        var registry = new AgentAdapterRegistry(new[]
        {
            FakeAgentAdapter.Throws("workbuddy", new InvalidOperationException("kaboom")),
            FakeAgentAdapter.Success("minimax"),
        }, NullLogger<AgentAdapterRegistry>.Instance);
        var state = new WorkerState(opts, new WorkerIdentity(opts));
        var channel = new ExecutionChannel(opts);
        var coord = new ExecutionCoordinator(store, inbox, channel, registry, state, NullLogger<ExecutionCoordinator>.Instance);

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
    // InFlightExecution record still carries (request, inbox_id) — used by
    // the Dispatcher when it pulls rows directly from the durable DB inbox
    // (the channel only carries a wake-signal sentinel in the round-7
    // DB-first architecture).
    // -------------------------------------------------------------------------

    [Fact]
    public void InFlightExecution_carries_request_and_inbox_id()
    {
        var req = new ExecutionRequest(
            ExecutionKey: "proposal:99:0:workbuddy",
            WorkloadType: "proposal",
            WorkloadId: 99,
            AgentType: "workbuddy",
            Round: 0,
            Source: "test",
            PayloadJson: "{}");
        var flight = new InFlightExecution(req, 42L);
        Assert.Equal("proposal:99:0:workbuddy", flight.Request.ExecutionKey);
        Assert.Equal(42L, flight.InboxId);
    }

    private static ExecutionRequest Req(string agent = "workbuddy", long id = 1) => new(
        ExecutionKey: $"proposal:{id}:0:{agent}",
        WorkloadType: "proposal",
        WorkloadId: id,
        AgentType: agent,
        Round: 0,
        Source: "test",
        PayloadJson: "{}");
}
