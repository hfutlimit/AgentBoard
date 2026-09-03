// SPDX-License-Identifier: MIT
using AgentBoard.Node;
using AgentBoard.Node.Agents;
using AgentBoard.Node.Execution;
using AgentBoard.Node.Tests.Fixtures;
using Microsoft.Data.Sqlite;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Xunit;

namespace AgentBoard.Node.Tests;

/// <summary>
/// Sprint 9. Fault-injection E2E tests. Happy-path unit tests saturate the
/// static-review surface; these tests exercise failure modes that cannot be
/// derived from code reading alone:
///
///   * SQLITE_BUSY during TryClaimAsync — does the new TryClaimOutcome
///     tri-state actually leave the row pending under a real concurrent
///     lock holder, or does the dispatcher still mis-classify it as a
///     completed task?
///   * 250-row backlog at production channel capacity 100 — does the
///     startup-recovery + idle-refill loop actually drain a backlog
///     larger than the channel, or do the tail rows strand in DB
///     pending forever?
///
/// Each test owns its own TempDbFixture so UNIQUE(execution_key) does
/// not leak between tests.
/// </summary>
public sealed class Sprint9_FaultInjectionTests
{
    // -------- shared helpers (kept local to avoid coupling to Sprint8) ----

    private static (IAgentAdapterRegistry Registry, FakeAgentAdapter Fake, ExecutionChannel Channel, WorkerState State)
        BuildStack(TempDbFixture fx, string agentType)
    {
        var fake = FakeAgentAdapter.Success(agentType);
        var registry = new AgentAdapterRegistry(new IAgentAdapter[] { fake }, NullLogger<AgentAdapterRegistry>.Instance);
        var channel = new ExecutionChannel(Options.Create(fx.Options));
        var identity = new WorkerIdentity(Options.Create(fx.Options));
        var state = new WorkerState(Options.Create(fx.Options), identity);
        return (registry, fake, channel, state);
    }

    private static async Task WaitForInboxTerminal(TempDbFixture fx, long inboxId, TimeSpan timeout)
    {
        var deadline = DateTimeOffset.UtcNow.Add(timeout);
        while (DateTimeOffset.UtcNow < deadline)
        {
            var row = await fx.Inbox.GetAsync(inboxId, CancellationToken.None);
            if (row is { Status: "completed" }) return;
            await Task.Delay(20);
        }
        throw new TimeoutException($"inbox row {inboxId} did not reach completed within {timeout}");
    }

    // -------------------------------------------------------------------------
    // SQLITE_BUSY during TryClaimAsync
    // -------------------------------------------------------------------------

    [Fact]
    public async Task Dispatcher_recovers_from_SQLITE_BUSY_during_claim()
    {
        // 2026-08-29 review follow-up: the previous dispatcher caught any
        // exception from TryClaimAsync and called MarkFailedAsync, which
        // unconditionally sets status='completed' and silently lost the
        // task. The new tri-state (Claimed / AlreadyClaimed /
        // TransientFailure) is supposed to keep the row in 'pending' and
        // retry on the next refill cycle. This test holds a real
        // SQLite RESERVED lock from a separate connection and asserts:
        //   1) while the lock is held, the dispatcher's claim returns
        //      TransientFailure (row stays pending, no adapter call)
        //   2) after the lock is released, the next idle-refill cycle
        //      claims the row and the adapter runs to completion
        using var fx = new TempDbFixture();
        var (registry, fake, channel, state) = BuildStack(fx, "fake");
        var coordinator = new ExecutionCoordinator(
            fx.Store, fx.Inbox, channel, registry, state,
            NullLogger<ExecutionCoordinator>.Instance);
        var dispatcher = new ExecutionDispatcher(
            channel, fx.Inbox, coordinator, NullLogger<ExecutionDispatcher>.Instance);

        // Insert a pending row. Default SQLite busy_timeout is 0
        // (no waiting), so the second connection's UPDATE will get
        // SQLITE_BUSY (5) immediately rather than block.
        var req = new ExecutionRequest(
            ExecutionKey: "busy-test",
            WorkloadType: "proposal",
            WorkloadId: 1,
            AgentType: "fake",
            Round: 0,
            Source: "fault-injection-busy",
            PayloadJson: "{}");
        var (inboxId, _) = await fx.Inbox.TryEnqueueAsync(req, CancellationToken.None);

        // Hold a RESERVED lock from a separate connection. RESERVED
        // doesn't block SELECT (so ListPendingAsync still works) but
        // blocks any UPDATE/INSERT from other connections → BUSY.
        using var blocker = new SqliteConnection(fx.Store.ConnectionString);
        await blocker.OpenAsync();
        await using (var begin = blocker.CreateCommand())
        {
            begin.CommandText = "BEGIN IMMEDIATE;";
            await begin.ExecuteNonQueryAsync();
        }

        // Start the dispatcher. Its claim path will hit SQLITE_BUSY
        // on every refill cycle while the blocker is alive.
        using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(15));
        await dispatcher.StartAsync(cts.Token);

        // Give the dispatcher a few refill cycles (IdleWakeInterval=2s)
        // to demonstrate the row is *not* marked completed.
        await Task.Delay(2500);

        var rowDuringBlock = await fx.Inbox.GetAsync(inboxId, CancellationToken.None);
        Assert.Equal("pending", rowDuringBlock!.Status);
        Assert.Equal(0, fake.CallCount);

        // Release the lock. The next refill cycle's TryClaimAsync
        // should succeed and the adapter should run.
        await using (var release = blocker.CreateCommand())
        {
            release.CommandText = "ROLLBACK;";
            await release.ExecuteNonQueryAsync();
        }

        // Wait for the row to reach completed. Give the dispatcher
        // a generous timeout — 8s covers up to four idle-wakeup cycles
        // (2s each) plus adapter execution.
        await WaitForInboxTerminal(fx, inboxId, TimeSpan.FromSeconds(8));
        await dispatcher.StopAsync(CancellationToken.None);

        Assert.Equal(1, fake.CallCount);
        var finalRow = await fx.Inbox.GetAsync(inboxId, CancellationToken.None);
        Assert.Equal("completed", finalRow!.Status);
    }

    // -------------------------------------------------------------------------
    // 250-row backlog at production channel capacity
    // -------------------------------------------------------------------------

    [Fact]
    public async Task Dispatcher_drains_250_row_backlog_at_production_capacity()
    {
        // 2026-08-29 review follow-up: with channel capacity 100 and a
        // 250-row crash backlog, the previous Dispatcher (which only
        // called ListPendingAsync at startup) stranded rows 101-250 in
        // DB pending forever. The new DB-pending-refill loop should
        // drain the entire backlog regardless of channel capacity.
        using var fx = new TempDbFixture();
        var (registry, fake, _, state) = BuildStack(fx, "fake");

        // Production-default channel capacity. Any deviation would
        // weaken the test (e.g. capacity=250 trivially passes).
        var channel = new ExecutionChannel(Options.Create(new NodeOptions
        {
            Id = "test-worker",
            DispatchChannelCapacity = 100,
        }));
        var coordinator = new ExecutionCoordinator(
            fx.Store, fx.Inbox, channel, registry, state,
            NullLogger<ExecutionCoordinator>.Instance);
        var dispatcher = new ExecutionDispatcher(
            channel, fx.Inbox, coordinator, NullLogger<ExecutionDispatcher>.Instance);

        const int N = 250;
        for (int i = 0; i < N; i++)
        {
            var req = new ExecutionRequest(
                ExecutionKey: $"backlog-{i}",
                WorkloadType: "proposal",
                WorkloadId: i,
                AgentType: "fake",
                Round: 0,
                Source: "backlog-250",
                PayloadJson: "{}");
            await fx.Inbox.TryEnqueueAsync(req, CancellationToken.None);
        }

        var pendingBefore = await fx.Inbox.ListPendingAsync(CancellationToken.None);
        Assert.Equal(N, pendingBefore.Count);

        // Start the dispatcher. It must drain all 250 rows via its
        // startup recovery + per-cycle DB-pending-refill loop.
        using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(60));
        await dispatcher.StartAsync(cts.Token);

        // Wait for the inbox to drain. 45s is generous: with 100-slot
        // channel + ~5ms per fake adapter call, the throughput is
        // 100/5ms = 20k/s, so 250 rows finish in <1s. The slack is for
        // CI hosts under load.
        var deadline = DateTimeOffset.UtcNow.AddSeconds(45);
        while (DateTimeOffset.UtcNow < deadline)
        {
            var pending = await fx.Inbox.ListPendingAsync(CancellationToken.None);
            if (pending.Count == 0) break;
            await Task.Delay(200);
        }

        await dispatcher.StopAsync(CancellationToken.None);

        var stillPending = await fx.Inbox.ListPendingAsync(CancellationToken.None);
        Assert.Empty(stillPending);
        Assert.Equal(N, fake.CallCount);
    }

    // -------------------------------------------------------------------------
    // 2026-08-29 review follow-up (round 9): TransientBackoff. When
    // SQLITE_BUSY / LOCKED persists across multiple drain attempts
    // (e.g. another connection holds a long-running transaction),
    // the round-8 design would fast-repoll the inbox and
    // TryClaimAsync on every cycle, burning the retry budget in
    // microseconds. This test holds a real RESERVED lock,
    // measures the inbox query rate over a 2-second window, and
    // asserts the rate is bounded by TransientBackoff (500 ms),
    // NOT by the time it takes to read the inbox.
    // -------------------------------------------------------------------------

    [Fact]
    public async Task Dispatcher_does_not_query_storm_under_sustained_BUSY()
    {
        // 2026-08-29 round-9: with the TransientBackoff path
        // the dispatcher should issue < 10 inbox queries in
        // 2 seconds while BUSY is sustained. Without the
        // backoff the round-8 design (CapHit → fast-repoll)
        // would issue thousands per second.
        using var fx = new TempDbFixture();
        var (registry, fake, channel, state) = BuildStack(fx, "fake");
        var coordinator = new ExecutionCoordinator(
            fx.Store, fx.Inbox, channel, registry, state,
            NullLogger<ExecutionCoordinator>.Instance);
        var dispatcher = new ExecutionDispatcher(
            channel, fx.Inbox, coordinator, NullLogger<ExecutionDispatcher>.Instance);

        // Insert a pending row.
        var req = new ExecutionRequest(
            ExecutionKey: "busy-storm",
            WorkloadType: "proposal",
            WorkloadId: 1,
            AgentType: "fake",
            Round: 0,
            Source: "storm-test",
            PayloadJson: "{}");
        var (inboxId, _) = await fx.Inbox.TryEnqueueAsync(req, CancellationToken.None);

        // Hold a RESERVED lock so every claim attempt hits BUSY.
        using var blocker = new SqliteConnection(fx.Store.ConnectionString);
        await blocker.OpenAsync();
        await using (var begin = blocker.CreateCommand())
        {
            begin.CommandText = "BEGIN IMMEDIATE;";
            await begin.ExecuteNonQueryAsync();
        }

        // Start the dispatcher. Use a longer timeout so the test
        // has time to enter the steady-state BUSY window.
        using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(15));
        await dispatcher.StartAsync(cts.Token);

        // Let the dispatcher settle into the BUSY loop. We
        // measure the inbox query rate over the next 2 s
        // window. With TransientBackoff=500ms, expect ≤ 8
        // queries (initial drain attempts + backoff retries).
        // Without the backoff the count grows into the
        // thousands.
        await Task.Delay(300);
        var baseline = fx.Inbox.GetOldestPendingFlightsCalls;
        await Task.Delay(2_000);
        var queriesUnderBusy = fx.Inbox.GetOldestPendingFlightsCalls - baseline;

        // The exact bound depends on how many TransientBackoff
        // retries the dispatcher does in 2 s. With 500 ms
        // backoff that's ≤ 4 retries + a few initial attempts.
        // 12 is a generous upper bound; the previous design
        // would produce thousands.
        Assert.True(queriesUnderBusy <= 12,
            $"query storm: {queriesUnderBusy} inbox queries in 2s under sustained BUSY; expected ≤ 12");

        // Sanity: the row must STILL be pending (not silently
        // marked failed by the round-8 path).
        var rowUnderBusy = await fx.Inbox.GetAsync(inboxId, CancellationToken.None);
        Assert.Equal("pending", rowUnderBusy!.Status);
        Assert.Equal(0, fake.CallCount);

        // Release the lock. The next TransientBackoff retry
        // should claim the row and run the adapter.
        await using (var release = blocker.CreateCommand())
        {
            release.CommandText = "ROLLBACK;";
            await release.ExecuteNonQueryAsync();
        }

        // Wait for the row to reach completed. With 500 ms
        // backoff the worst-case wait is one backoff cycle
        // (≤ 1 s) plus adapter execution. Give it 8 s of
        // slack.
        await WaitForInboxTerminal(fx, inboxId, TimeSpan.FromSeconds(8));
        await dispatcher.StopAsync(CancellationToken.None);

        Assert.Equal(1, fake.CallCount);
    }
}
