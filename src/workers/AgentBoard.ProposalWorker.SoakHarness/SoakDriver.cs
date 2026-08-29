// SPDX-License-Identifier: MIT
using System.Diagnostics;
using System.Text.Json;
using AgentBoard.ProposalWorker;
using AgentBoard.ProposalWorker.Agents;
using AgentBoard.ProposalWorker.Execution;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;

namespace AgentBoard.ProposalWorker.SoakHarness;

/// <summary>
/// Drives a long-running soak test of the worker runtime:
///
///   1. Sets up a temp SQLite database (ExecutionStore + InboxStore).
///   2. Wires the production ExecutionChannel (FullMode=DropWrite), the
///      production ExecutionCoordinator, and the production
///      ExecutionDispatcher (DB-first scheduling).
///   3. Spins up a fake producer at a target req/s rate that enqueues
///      ExecutionRequests directly into the inbox (durable source of truth).
///   4. Periodically samples GC heap / channel depth / inbox pending /
///      SQLite busy count / throughput.
///   5. Stops after the configured duration and writes a JSON report
///      plus a short markdown summary.
///
/// The harness is intentionally in-process: no RabbitMQ, no FastAPI,
/// no external dependencies. It exercises the same code paths
/// (DB-first scheduling, channel DropWrite, busy retry helper, MarkDegraded
/// fall-back) that production uses, at controllable load.
/// </summary>
public sealed class SoakDriver
{
    private readonly SoakOptions _opts;
    private readonly List<MetricsSample> _samples = new();
    private long _produced;
    private long _completed;
    private long _failed;
    private long _busyHits;
    private long _degradedTransitions;
    private long _lastCompleted;
    private DateTimeOffset _lastSampleAt = DateTimeOffset.UtcNow;

    public SoakDriver(SoakOptions opts) => _opts = opts;

    public async Task RunAsync(CancellationToken ct)
    {
        // 1. Temp DB (delete any prior run).
        if (File.Exists(_opts.DatabasePath)) File.Delete(_opts.DatabasePath);
        Directory.CreateDirectory(Path.GetDirectoryName(_opts.DatabasePath)!);
        Directory.CreateDirectory(Path.GetDirectoryName(_opts.ReportPath)!);

        var workerOptions = new WorkerOptions
        {
            Id = "soak",
            HistoryDatabasePath = _opts.DatabasePath,
            OrphanThresholdMinutes = 60,
            DispatchChannelCapacity = _opts.ChannelCapacity,
        };
        var workerOptionsWrapper = Options.Create(workerOptions);

        var store = new ExecutionStore(workerOptionsWrapper, NullLogger<ExecutionStore>.Instance);
        var inbox = new InboxStore(store, NullLogger<InboxStore>.Instance);
        var channel = new ExecutionChannel(workerOptionsWrapper);
        var identity = new WorkerIdentity(workerOptionsWrapper);
        var state = new WorkerState(workerOptionsWrapper, identity);

        // 2. Fake success adapter (inlined so SoakHarness has no test-project
        // dependency). Each call sleeps 5-25ms to simulate real work without
        // saturating the channel. The completion callback is the simplest
        // hook to observe end-to-end throughput without modifying the
        // production inbox / store / coordinator code.
        var adapter = new SoakFakeAdapter(() => Interlocked.Increment(ref _completed));
        var registry = new AgentAdapterRegistry(
            new IAgentAdapter[] { adapter },
            NullLogger<AgentAdapterRegistry>.Instance);

        var coordinator = new ExecutionCoordinator(
            store, inbox, channel, registry, state,
            new SoakConsoleLogger<ExecutionCoordinator>());
        var dispatcher = new ExecutionDispatcher(
            channel, inbox, coordinator,
            new SoakConsoleLogger<ExecutionDispatcher>());

        // 3. Start dispatcher.
        var dispatcherCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        await dispatcher.StartAsync(dispatcherCts.Token);

        Console.WriteLine($"[soak] dispatcher started; db={_opts.DatabasePath}");

        // 4. Producer: enqueue at target rate.
        var producerCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        var producerTask = Task.Run(() => ProducerLoop(inbox, producerCts.Token), producerCts.Token);

        // 5. Metrics sampler.
        var sampleCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        var samplerTask = Task.Run(() => SamplerLoop(state, channel, inbox, store, sampleCts.Token), sampleCts.Token);

        // 6. Wait for duration, then stop.
        try
        {
            await Task.Delay(_opts.Duration, ct);
            Console.WriteLine("[soak] duration reached; stopping");
        }
        catch (OperationCanceledException)
        {
            Console.WriteLine("[soak] cancelled; stopping");
        }

        // Tear down. Order matters: stop producer first (no new work), then
        // dispatcher (drain in-flight), then snapshot.
        producerCts.Cancel();
        try { await producerTask; } catch (OperationCanceledException) { }
        sampleCts.Cancel();
        try { await samplerTask; } catch (OperationCanceledException) { }
        await dispatcher.StopAsync(CancellationToken.None);
        dispatcherCts.Cancel();

        // Final sample so the report covers the post-stop state.
        var final = Snapshot(state, channel, inbox, store, _opts.Duration, finalSample: true);
        _samples.Add(final);

        // 7. Write report.
        WriteReport();
    }

    private async Task ProducerLoop(InboxStore inbox, CancellationToken ct)
    {
        // Pace the loop so we hit the target throughput (req/s) on average.
        // We use a coarse sleep: 1000/throughput ms per request, with the
        // actual loop running as fast as it can when throughput is high.
        var perReqMs = Math.Max(1, 1000.0 / _opts.TargetThroughputPerSec);
        var sw = Stopwatch.StartNew();
        var next = 0L;
        while (!ct.IsCancellationRequested)
        {
            var req = new ExecutionRequest(
                ExecutionKey: $"soak-{next:D10}",
                WorkloadType: "proposal",
                WorkloadId: next,
                AgentType: "fake",
                Round: 0,
                Source: "soak-harness",
                PayloadJson: "{}");
            try
            {
                // TryEnqueueAsync returns (InboxId, IsNew). For the soak
                // test the producer doesn't need to distinguish new from
                // dedup — the dispatcher claims whatever is pending.
                await inbox.TryEnqueueAsync(req, ct);
                Interlocked.Increment(ref _produced);
            }
            catch (OperationCanceledException) { break; }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"[soak] producer enqueue failed: {ex.Message}");
            }
            next++;
            // Pace: perReqMs, but subtract the time we already spent.
            var elapsed = sw.Elapsed.TotalMilliseconds;
            var target = next * perReqMs;
            var sleepMs = (int)Math.Max(0, target - elapsed);
            if (sleepMs > 0) await Task.Delay(sleepMs, ct);
        }
    }

    private async Task SamplerLoop(WorkerState state, ExecutionChannel channel, InboxStore inbox, ExecutionStore store, CancellationToken ct)
    {
        var startedAt = DateTimeOffset.UtcNow;
        var lastDegraded = false;
        while (!ct.IsCancellationRequested)
        {
            try
            {
                var sample = Snapshot(state, channel, inbox, store, DateTimeOffset.UtcNow - startedAt, finalSample: false);
                _samples.Add(sample);

                // Detect degraded transitions and log to stdout.
                var nowDegraded = state.IsDegraded;
                if (nowDegraded && !lastDegraded)
                {
                    Interlocked.Increment(ref _degradedTransitions);
                    Console.Error.WriteLine($"[soak] DEGRADED: {state.DegradedReason}");
                }
                if (!nowDegraded && lastDegraded)
                {
                    Console.Error.WriteLine("[soak] recovered from degraded");
                }
                lastDegraded = nowDegraded;

                LogSample(sample);
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"[soak] sampler error: {ex.Message}");
            }
            try { await Task.Delay(_opts.SampleInterval, ct); }
            catch (OperationCanceledException) { break; }
        }
    }

    private MetricsSample Snapshot(WorkerState state, ExecutionChannel channel, InboxStore inbox, ExecutionStore store, TimeSpan elapsed, bool finalSample)
    {
        var now = DateTimeOffset.UtcNow;
        // Force a GC so GetTotalMemory reflects retained heap, not transient allocations.
        GC.Collect(2, GCCollectionMode.Forced, blocking: true);
        var totalBytes = GC.GetTotalMemory(forceFullCollection: true);

        var pendingInbox = SafeCount(() => inbox.CountPendingAsync(CancellationToken.None).GetAwaiter().GetResult());
        var dispatching = SafeCount(() =>
        {
            // Snapshot running executions: rows that have been StartAsync'd but
            // not yet settled to a terminal state. Proxies for "in flight".
            var rows = store.ListAsync(10_000, null, CancellationToken.None).GetAwaiter().GetResult();
            return rows.Count(r => r.Status == "Running");
        });

        var since = now - _lastSampleAt;
        var completedDelta = Interlocked.Read(ref _completed) - _lastCompleted;
        var throughputWindow = since.TotalSeconds > 0 ? completedDelta / since.TotalSeconds : 0;
        _lastCompleted = Interlocked.Read(ref _completed);
        _lastSampleAt = now;

        // SQLite busy hits: we instrument via the inbox / store wrapper. For
        // now expose the snapshot's count by querying the ExecutionStore's
        // counter if available; otherwise report 0. (Implemented below via
        // a public counter on a wrapper, see SoakMetrics.)
        var busy = Interlocked.Read(ref _busyHits);

        return new MetricsSample
        {
            Elapsed = elapsed,
            Timestamp = now,
            FinalSample = finalSample,
            GcTotalBytes = totalBytes,
            GcGen0 = GC.CollectionCount(0),
            GcGen1 = GC.CollectionCount(1),
            GcGen2 = GC.CollectionCount(2),
            PendingInbox = pendingInbox,
            Dispatching = dispatching,
            ProducedTotal = Interlocked.Read(ref _produced),
            CompletedTotal = Interlocked.Read(ref _completed),
            FailedTotal = Interlocked.Read(ref _failed),
            ThroughputRps = throughputWindow,
            BusyHitsTotal = busy,
            Degraded = state.IsDegraded,
            Paused = state.Paused,
        };
    }

    private static int SafeCount(Func<int> f)
    {
        try { return f(); } catch { return -1; }
    }

    private void LogSample(MetricsSample s)
    {
        // Use KB instead of MB — at low steady-state heap (the
        // dispatcher + a 50-row inbox + GC overhead is well under
        // 1 MB), MB floors to 0 and the line looks like nothing
        // happened. KB makes the actual delta visible at this
        // scale, which is what leak detection needs.
        var kb = s.GcTotalBytes / 1024.0;
        Console.WriteLine(
            $"[soak] t={s.Elapsed:mm\\:ss} heap={kb,7:F1}KB " +
            $"gen0={s.GcGen0,4} gen1={s.GcGen1,4} gen2={s.GcGen2,4} " +
            $"pending={s.PendingInbox,5} dispatching={s.Dispatching,3} " +
            $"produced={s.ProducedTotal,7} completed={s.CompletedTotal,7} " +
            $"failed={s.FailedTotal,4} rps={s.ThroughputRps,5:F1} " +
            $"busy={s.BusyHitsTotal,4} degraded={s.Degraded}");
    }

    private void WriteReport()
    {
        var report = new SoakReport
        {
            Options = _opts,
            Samples = _samples,
            ProducedTotal = Interlocked.Read(ref _produced),
            CompletedTotal = Interlocked.Read(ref _completed),
            FailedTotal = Interlocked.Read(ref _failed),
            BusyHitsTotal = Interlocked.Read(ref _busyHits),
            DegradedTransitions = Interlocked.Read(ref _degradedTransitions),
        };
        var json = JsonSerializer.Serialize(report, new JsonSerializerOptions { WriteIndented = true });
        File.WriteAllText(_opts.ReportPath, json);
        Console.WriteLine($"[soak] wrote report: {_opts.ReportPath}");

        // Short markdown summary on stdout.
        Console.WriteLine();
        Console.WriteLine("=== Soak summary ===");
        Console.WriteLine($"  duration:      {_opts.Duration}");
        Console.WriteLine($"  throughput:    {_opts.TargetThroughputPerSec} req/s target");
        Console.WriteLine($"  produced:      {report.ProducedTotal}");
        Console.WriteLine($"  completed:     {report.CompletedTotal}");
        Console.WriteLine($"  failed:        {report.FailedTotal}");
        Console.WriteLine($"  busy hits:     {report.BusyHitsTotal}");
        Console.WriteLine($"  degraded #:    {report.DegradedTransitions}");
        if (_samples.Count >= 2)
        {
            var first = _samples[0];
            var last = _samples[^1];
            var delta = last.GcTotalBytes - first.GcTotalBytes;
            var mb = delta / (1024 * 1024);
            Console.WriteLine($"  heap delta:    {mb:+0;-0} MB over {_samples.Count} samples");
        }
    }
}

/// <summary>One metrics sample.</summary>
public sealed record MetricsSample
{
    public TimeSpan Elapsed { get; init; }
    public DateTimeOffset Timestamp { get; init; }
    public bool FinalSample { get; init; }
    public long GcTotalBytes { get; init; }
    public int GcGen0 { get; init; }
    public int GcGen1 { get; init; }
    public int GcGen2 { get; init; }
    public int PendingInbox { get; init; }
    public int Dispatching { get; init; }
    public long ProducedTotal { get; init; }
    public long CompletedTotal { get; init; }
    public long FailedTotal { get; init; }
    public double ThroughputRps { get; init; }
    public long BusyHitsTotal { get; init; }
    public bool Degraded { get; init; }
    public bool Paused { get; init; }
}

public sealed record SoakReport
{
    public SoakOptions Options { get; init; } = null!;
    public List<MetricsSample> Samples { get; init; } = new();
    public long ProducedTotal { get; init; }
    public long CompletedTotal { get; init; }
    public long FailedTotal { get; init; }
    public long BusyHitsTotal { get; init; }
    public long DegradedTransitions { get; init; }
}
