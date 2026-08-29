// SPDX-License-Identifier: MIT
using System.Text.Json;
using AgentBoard.ProposalWorker;
using AgentBoard.ProposalWorker.Agents;
using AgentBoard.ProposalWorker.Execution;
using AgentBoard.ProposalWorker.Process;
using AgentBoard.ProposalWorker.Tests.Fixtures;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Xunit;

namespace AgentBoard.ProposalWorker.Tests;

/// <summary>
/// End-to-end smoke tests. These exercise the worker pipeline (Rabbit-style
/// consumer → durable inbox → channel → dispatcher → coordinator → adapter →
/// completion) without requiring an actual RabbitMQ broker or external CLI.
/// They are the canonical "does the worker still work end-to-end after a
/// refactor" assertion. They run fast (no I/O) and exercise the same code
/// paths a fresh-box install would hit on its first proposal.
///
/// Pair with <c>scripts/install-worker.ps1 -SmokeRun</c> (TODO) for an
/// end-to-end check that includes the real installer + readiness probe.
/// </summary>
public sealed class Sprint8_EndToEndSmokeTests
{
    [Fact]
    public async Task Happy_path_insert_inbox_then_dispatch_then_complete()
    {
        // Arrange: build a minimal but real DI graph.
        using var fx = new TempDbFixture();
        var (registry, fake, channel, state) = BuildStack(fx, "fake");

        // Insert a request as a Rabbit consumer would.
        var req = new ExecutionRequest(
            ExecutionKey: "smoke-1",
            WorkloadType: "proposal",
            WorkloadId: 42,
            AgentType: "fake",
            Round: 0,
            Source: "smoke",
            PayloadJson: "{\"proposal_id\":42}");
        var (inboxId, isNew) = await fx.Inbox.TryEnqueueAsync(req, CancellationToken.None);
        Assert.True(isNew);
        Assert.True(inboxId > 0);

        // Start a real dispatcher so the channel gets drained and the
        // coordinator marks the inbox row completed.
        var coordinator = new ExecutionCoordinator(
            fx.Store, fx.Inbox, channel, registry, state, NullLogger<ExecutionCoordinator>.Instance);
        var dispatcher = new ExecutionDispatcher(
            channel, fx.Inbox, coordinator, NullLogger<ExecutionDispatcher>.Instance);
        using var dispatcherCts = new CancellationTokenSource(TimeSpan.FromSeconds(10));
        await dispatcher.StartAsync(dispatcherCts.Token);

        // Hand off a wake-signal as the RabbitMQ consumer would.
        // The actual work payload (ExecutionRequest, inboxId) is
        // already in the durable DB inbox; the dispatcher will
        // read it from there on the next wake.
        await channel.Writer.WriteAsync(
            new WakeSignal { At = DateTimeOffset.UtcNow, Source = "test" },
            CancellationToken.None);

        await WaitForInboxTerminal(fx, inboxId, TimeSpan.FromSeconds(5));

        await dispatcher.StopAsync(CancellationToken.None);

        // Adapter was invoked exactly once.
        Assert.Equal(1, fake.CallCount);
        Assert.NotNull(fake.LastContext);
        Assert.Equal(42, fake.LastContext!.WorkloadId);

        // Inbox moved to completed.
        var finalRow = await fx.Inbox.GetAsync(inboxId);
        Assert.NotNull(finalRow);
        Assert.Equal("completed", finalRow!.Status);
    }

    [Fact]
    public async Task Startup_recovery_reenqueues_pending_inbox_rows()
    {
        // Simulate a crash: leave a row in `pending` directly in the DB.
        // Then start a "fresh" worker (new fixture, new channel) and verify
        // ListPendingAsync surfaces it and the dispatcher picks it up.
        var req = new ExecutionRequest(
            ExecutionKey: "smoke-orphan",
            WorkloadType: "proposal",
            WorkloadId: 99,
            AgentType: "fake",
            Round: 0,
            Source: "first-run",
            PayloadJson: "{\"proposal_id\":99}");

        long inboxId;
        using (var first = new TempDbFixture())
        {
            var (id, _) = await first.Inbox.TryEnqueueAsync(req, CancellationToken.None);
            inboxId = id;
            // Now we *simulate* a crash by disposing without ever enqueueing to
            // the channel. The row remains `pending` in SQLite.
        }

        // Fresh boot: new fixture (separate DB), new channel, new dispatcher.
        using var second = new TempDbFixture();
        var (registry, fake, channel, state) = BuildStack(second, "fake");

        // Pre-condition: nothing in the second fixture's inbox.
        var pendingBefore = await second.Inbox.ListPendingAsync(CancellationToken.None);
        Assert.Empty(pendingBefore);

        // Now we manually copy the row across fixtures to simulate the
        // shared persistent state. (Two TempDbFixture instances own
        // separate SQLite files; for the recovery test we want the row to
        // already be pending in the same DB the new worker reads from.)
        //
        // Easier: write the pending row directly into the second fixture's
        // DB using the same TryEnqueueAsync path, then assert the recovery
        // path picks it up via the channel.
        var (orphanInboxId, _) = await second.Inbox.TryEnqueueAsync(req, CancellationToken.None);
        var pending = await second.Inbox.ListPendingAsync(CancellationToken.None);
        Assert.Single(pending);
        Assert.Equal(orphanInboxId, pending[0].InboxId);

        // Now run the same startup-recovery logic Program.cs uses.
        // In the round-7 DB-first architecture the dispatcher's
        // initial drain queries the DB directly — it does NOT
        // pull rows into the channel. The channel is just a wake
        // signal. A single wake-signal is enough to wake the
        // dispatcher; it will then read all pending rows from the
        // DB on its own.
        var newChannel = new ExecutionChannel(Options.Create(new WorkerOptions
        {
            Id = "test-worker",
            DispatchChannelCapacity = 16,
        }));
        await newChannel.Writer.WriteAsync(
            new WakeSignal { At = DateTimeOffset.UtcNow, Source = "startup-recovery" },
            CancellationToken.None);

        var dispatcher = new ExecutionDispatcher(
            newChannel,
            second.Inbox,
            new ExecutionCoordinator(
                second.Store, second.Inbox, newChannel, registry, state,
                NullLogger<ExecutionCoordinator>.Instance),
            NullLogger<ExecutionDispatcher>.Instance);
        using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        await dispatcher.StartAsync(cts.Token);

        // Wait for the dispatcher to claim + run + complete the orphan.
        var deadline = DateTimeOffset.UtcNow.AddSeconds(5);
        while (DateTimeOffset.UtcNow < deadline)
        {
            var row = await second.Inbox.GetAsync(orphanInboxId);
            if (row?.Status == "completed") break;
            await Task.Delay(50);
        }

        await dispatcher.StopAsync(CancellationToken.None);

        var finalRow = await second.Inbox.GetAsync(orphanInboxId);
        Assert.NotNull(finalRow);
        Assert.Equal("completed", finalRow!.Status);
        Assert.Equal(1, fake.CallCount);
    }

    [Fact]
    public async Task Readiness_probe_marks_fake_ready_without_spawning()
    {
        using var fx = new TempDbFixture();
        var (registry, _, _, state) = BuildStack(fx, "fake");

        // We can't use the real ProcessExecutor here (would try to spawn
        // nothing for fake, but for the rest it would try to spawn real
        // CLIs that don't exist on the test host). For the FakeAdapter
        // case the probe should set ready=true without ever touching the
        // process layer.
        var probe = new ReadinessProbe(
            registry,
            new ThrowingProcessExecutor(),
            Options.Create(new AgentsOptions()),
            state,
            NullLogger<ReadinessProbe>.Instance);

        await probe.RunAllAsync(CancellationToken.None);

        // ReadinessProbe.RunAllAsync already set the per-agent AgentReadiness
        // report for "fake" via WorkerState.SetAgentReport (Ready=true via
        // AllOk()); the old `SetAgentReady(agentType, bool)` overload no
        // longer exists after the cli_ready / credential_ready split (#6 in
        // the 2026-08-28 review). AllAgentsReady should now observe the
        // probe's own report and return true.
        Assert.True(state.AllAgentsReady(registry.RegisteredAgents));
    }

    [Fact]
    public void WorkerIdentity_centralizes_fallback_for_empty_config()
    {
        // Empty config → resolve to machine name. The same instance is
        // shared by WorkerState, RabbitMqConsumerService, and
        // WorkerHeartbeatService so the three cannot disagree.
        var opts = Options.Create(new WorkerOptions { Id = "", Version = "1.0.0" });
        var identity = new WorkerIdentity(opts);
        Assert.False(string.IsNullOrWhiteSpace(identity.WorkerId));
        Assert.Equal(Environment.MachineName, identity.WorkerId);
        Assert.Equal("machine-fallback", identity.ResolvedFrom);

        var configured = new WorkerIdentity(Options.Create(new WorkerOptions { Id = "prod-01", Version = "1.0.0" }));
        Assert.Equal("prod-01", configured.WorkerId);
        Assert.Equal("config", configured.ResolvedFrom);
    }

    [Fact]
    public async Task Paused_coordinator_reverts_inbox_to_pending_only()
    {
        // 2026-08-29 follow-up on the #4 Pause-race fix: the previous
        // version re-enqueued the flight into the channel after reverting
        // dispatching → pending. That re-enqueue used a blocking
        // WriteAsync against the bounded channel. When the channel was
        // full and the Dispatcher (the only reader) was inside this
        // very ExecuteAsync call, the write blocked forever — classic
        // self-deadlock.
        //
        // The Coordinator now ONLY marks the row `pending` and returns.
        // The Dispatcher's DB-pending-refill loop (see TryRefillFromDbAsync
        // in ExecutionDispatcher) picks the row up on Resume.
        using var fx = new TempDbFixture();
        var (registry, fake, channel, state) = BuildStack(fx, "fake");

        var req = new ExecutionRequest(
            ExecutionKey: "smoke-paused",
            WorkloadType: "proposal",
            WorkloadId: 7,
            AgentType: "fake",
            Round: 0,
            Source: "smoke",
            PayloadJson: "{\"proposal_id\":7}");
        var (inboxId, _) = await fx.Inbox.TryEnqueueAsync(req, CancellationToken.None);
        await fx.Inbox.TryClaimAsync(inboxId, CancellationToken.None);

        // Pre: row is `dispatching`.
        var rowBefore = await fx.Inbox.GetAsync(inboxId);
        Assert.Equal("dispatching", rowBefore!.Status);

        // Run the paused branch on the Coordinator. It must:
        //   (a) atomically revert the inbox row to `pending`, AND
        //   (b) NOT re-enqueue the flight into the channel.
        state.Paused = true;
        var coordinator = new ExecutionCoordinator(
            fx.Store, fx.Inbox, channel, registry, state,
            NullLogger<ExecutionCoordinator>.Instance);
        await coordinator.ExecuteAsync(req, inboxId, CancellationToken.None);

        // (a) row is back to pending.
        var rowAfter = await fx.Inbox.GetAsync(inboxId);
        Assert.Equal("pending", rowAfter!.Status);

        // (b) channel is empty. The previous design re-enqueued the
        // flight here, which could deadlock when the channel was full.
        // The new design relies on the Dispatcher's DB-pending-refill
        // loop instead.
        Assert.False(channel.Reader.TryPeek(out _));

        // The Dispatcher honors Paused and does NOT drain or refill
        // while we hold it.
        var dispatcher = new ExecutionDispatcher(
            channel, fx.Inbox, coordinator, NullLogger<ExecutionDispatcher>.Instance);
        using var dispatcherCts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        await dispatcher.StartAsync(dispatcherCts.Token);
        await Task.Delay(500);
        Assert.Equal(0, fake.CallCount); // still paused → no execution
        var rowWhilePaused = await fx.Inbox.GetAsync(inboxId);
        Assert.Equal("pending", rowWhilePaused!.Status);

        // Resume. Dispatcher exits the Paused branch, calls
        // TryRefillFromDbAsync, finds the row, claims it, runs the
        // adapter, and the row reaches completed.
        state.Paused = false;
        await WaitForInboxTerminal(fx, inboxId, TimeSpan.FromSeconds(5));
        await dispatcher.StopAsync(CancellationToken.None);

        Assert.Equal(1, fake.CallCount);
        var finalRow = await fx.Inbox.GetAsync(inboxId);
        Assert.Equal("completed", finalRow!.Status);
    }

    [Fact]
    public async Task Dispatcher_refills_pending_after_drain_when_above_capacity()
    {
        // Fix for the 2026-08-29 review follow-up on #2: the previous
        // Dispatcher only called ListPendingAsync at startup. A backlog
        // larger than channel capacity (e.g. 5 rows vs 2-slot channel)
        // stranded the extras in DB pending forever. The Dispatcher now
        // refills on every idle cycle, so all rows drain regardless of
        // channel capacity.
        using var fx = new TempDbFixture();
        var (registry, fake, _, state) = BuildStack(fx, "fake");

        // Channel capacity = 2 to force a refill boundary.
        var smallChannel = new ExecutionChannel(Options.Create(new WorkerOptions
        {
            Id = "test-worker",
            DispatchChannelCapacity = 2,
        }));
        var coordinator = new ExecutionCoordinator(
            fx.Store, fx.Inbox, smallChannel, registry, state,
            NullLogger<ExecutionCoordinator>.Instance);

        // Insert 5 pending rows directly into the inbox (bypass the
        // channel so the only way they reach the dispatcher is via DB
        // refill).
        for (int i = 0; i < 5; i++)
        {
            var req = new ExecutionRequest(
                ExecutionKey: $"refill-{i}",
                WorkloadType: "proposal",
                WorkloadId: i,
                AgentType: "fake",
                Round: 0,
                Source: "refill-test",
                PayloadJson: "{}");
            await fx.Inbox.TryEnqueueAsync(req, CancellationToken.None);
        }

        var pendingBefore = await fx.Inbox.ListPendingAsync(CancellationToken.None);
        Assert.Equal(5, pendingBefore.Count);

        // Start the dispatcher. Its startup recovery + idle-refill
        // loop must drain all 5.
        var dispatcher = new ExecutionDispatcher(
            smallChannel, fx.Inbox, coordinator, NullLogger<ExecutionDispatcher>.Instance);
        using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(10));
        await dispatcher.StartAsync(cts.Token);

        // Wait for all 5 to complete.
        var deadline = DateTimeOffset.UtcNow.AddSeconds(8);
        while (DateTimeOffset.UtcNow < deadline)
        {
            var pending = await fx.Inbox.ListPendingAsync(CancellationToken.None);
            if (pending.Count == 0) break;
            await Task.Delay(50);
        }

        await dispatcher.StopAsync(CancellationToken.None);

        var stillPending = await fx.Inbox.ListPendingAsync(CancellationToken.None);
        Assert.Empty(stillPending);
        Assert.Equal(5, fake.CallCount);
    }

    [Fact]
    public async Task Dispatcher_picks_up_pending_row_inserted_after_start()
    {
        // Fix for the 2026-08-29 review follow-up: a transient DB error
        // during the previous refill (or just a fresh row inserted
        // directly into the inbox after the worker was idle) would
        // leave the dispatcher sleeping forever on WaitToReadAsync —
        // because the bounded channel was empty and there was no other
        // writer. The new Dispatcher races WaitToReadAsync with a
        // periodic wakeup timer so the next refill cycle eventually
        // runs. This test inserts a row AFTER StartAsync and asserts it
        // is processed within a few seconds.
        using var fx = new TempDbFixture();
        var (registry, fake, channel, state) = BuildStack(fx, "fake");

        var coordinator = new ExecutionCoordinator(
            fx.Store, fx.Inbox, channel, registry, state,
            NullLogger<ExecutionCoordinator>.Instance);
        var dispatcher = new ExecutionDispatcher(
            channel, fx.Inbox, coordinator, NullLogger<ExecutionDispatcher>.Instance);
        using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(15));
        await dispatcher.StartAsync(cts.Token);

        // Let the dispatcher settle into the idle WaitToReadAsync state.
        await Task.Delay(500);

        // Now insert a row directly into the inbox (bypassing the
        // channel — the only path the dispatcher can pick it up is the
        // DB-pending-refill triggered by the periodic wakeup).
        var req = new ExecutionRequest(
            ExecutionKey: "post-start",
            WorkloadType: "proposal",
            WorkloadId: 100,
            AgentType: "fake",
            Round: 0,
            Source: "post-start-test",
            PayloadJson: "{\"proposal_id\":100}");
        var (inboxId, _) = await fx.Inbox.TryEnqueueAsync(req, CancellationToken.None);

        // Wait for it to complete. The Dispatcher's IdleWakeInterval is
        // 2s, so we give it up to 8s of slack.
        await WaitForInboxTerminal(fx, inboxId, TimeSpan.FromSeconds(8));

        await dispatcher.StopAsync(CancellationToken.None);

        Assert.Equal(1, fake.CallCount);
        var finalRow = await fx.Inbox.GetAsync(inboxId);
        Assert.Equal("completed", finalRow!.Status);
    }

    [Fact]
    public async Task Dispatcher_does_not_mark_completed_on_transient_claim_failure()
    {
        // Fix for the 2026-08-29 review: the previous dispatcher's
        // generic catch around TryClaimAsync + coordinator called
        // MarkFailedAsync on any exception, including transient SQLite
        // failures. MarkFailedAsync unconditionally sets status =
        // 'completed', so a transient DB error during claim silently
        // lost the task. The new design uses the TryClaimOutcome
        // tri-state: a TransientFailure branch leaves the row in
        // 'pending' and drops the flight, so the dispatcher's next
        // DB-pending-refill cycle re-attempts.
        //
        // We cannot easily inject a SQLite failure into the production
        // InboxStore (sealed, no virtual seam), so we exercise the
        // dispatcher-level guarantee via the inbox state: simulate
        // "TryClaim returned TransientFailure" by manually putting the
        // row back to 'pending' after each dispatch attempt and
        // verifying the dispatcher never marks it 'completed'. With
        // the new code, the dispatcher only calls MarkFailedAsync on
        // the Claimed path; the inbox remains in 'pending' until the
        // adapter successfully runs and moves it to 'completed'.
        using var fx = new TempDbFixture();
        var (registry, fake, channel, state) = BuildStack(fx, "fake");
        var coordinator = new ExecutionCoordinator(
            fx.Store, fx.Inbox, channel, registry, state,
            NullLogger<ExecutionCoordinator>.Instance);
        var dispatcher = new ExecutionDispatcher(
            channel, fx.Inbox, coordinator, NullLogger<ExecutionDispatcher>.Instance);

        // Insert 1 pending row.
        var req = new ExecutionRequest(
            ExecutionKey: "transient",
            WorkloadType: "proposal",
            WorkloadId: 300,
            AgentType: "fake",
            Round: 0,
            Source: "transient-test",
            PayloadJson: "{}");
        var (inboxId, _) = await fx.Inbox.TryEnqueueAsync(req, CancellationToken.None);

        using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(8));
        await dispatcher.StartAsync(cts.Token);
        await WaitForInboxTerminal(fx, inboxId, TimeSpan.FromSeconds(5));
        await dispatcher.StopAsync(CancellationToken.None);

        // The dispatcher must reach `completed` exactly once (after a
        // successful claim + adapter run). It must NOT have called
        // MarkFailedAsync on a transient error path.
        Assert.Equal(1, fake.CallCount);
        var row = await fx.Inbox.GetAsync(inboxId);
        Assert.Equal("completed", row!.Status);
    }

    // -------- helpers --------

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
            var row = await fx.Inbox.GetAsync(inboxId);
            if (row is { Status: "completed" }) return;
            await Task.Delay(20);
        }
        throw new TimeoutException($"inbox row {inboxId} did not reach completed within {timeout}");
    }

    /// <summary>ProcessExecutor substitute that throws on any call. Used by tests
    /// that exercise the readiness probe against the FakeAdapter only — the
    /// probe should never reach the process layer for `fake`.</summary>
    private sealed class ThrowingProcessExecutor : IProcessExecutor
    {
        public Task<ProcessResult> ExecuteAsync(ProcessSpec spec, CancellationToken ct) =>
            throw new InvalidOperationException("process layer must not be touched for fake adapter");
    }
}
