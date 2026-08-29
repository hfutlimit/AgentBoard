// SPDX-License-Identifier: MIT
using System.Diagnostics;
using System.Text.Json;
using AgentBoard.ProposalWorker.Agents;

namespace AgentBoard.ProposalWorker.SoakHarness;

/// <summary>
/// Fake success adapter for the soak harness. Sleeps 5-25 ms to
/// simulate real agent work, then returns a synthetic decision. Tracks
/// call count and total elapsed so the metrics layer can derive
/// throughput and validate the dispatcher actually drained the inbox.
/// </summary>
public sealed class SoakFakeAdapter : IAgentAdapter
{
    private long _callCount;
    private long _totalElapsedTicks;
    private readonly Action _onCompleted;
    private readonly Random _rng = Random.Shared;

    public SoakFakeAdapter(Action onCompleted) => _onCompleted = onCompleted;

    public string AgentType => "fake";

    public async Task<AgentExecutionResult> ExecuteAsync(ExecutionContext context, CancellationToken ct)
    {
        var sw = Stopwatch.StartNew();
        // Random sleep 0-3 ms to simulate very light agent work. The
        // production WorkBuddy adapter spends seconds per call, but
        // for a soak test we want a tight loop so the dispatcher's
        // own scheduling / DB-poll / wake-signal path is exercised
        // at maximum rate without us spending the wall clock waiting
        // on a real LLM. The channel + DB-first + MarkSucceeded
        // paths all run per call regardless of adapter latency.
        var delayMs = _rng.Next(0, 4);
        if (delayMs > 0) await Task.Delay(delayMs, ct);
        sw.Stop();
        Interlocked.Increment(ref _callCount);
        Interlocked.Add(ref _totalElapsedTicks, sw.ElapsedTicks);

        // Notify the driver that the agent side is done. The actual
        // inbox → completed transition happens after this in
        // ExecutionCoordinator.MarkSucceeded; we count "agent finished"
        // here which is a reasonable proxy for end-to-end throughput
        // (MarkSucceeded almost never fails in the soak).
        _onCompleted();

        var decision = new
        {
            soak = true,
            work_id = context.WorkloadId,
            agent = context.AgentType,
            delay_ms = delayMs,
        };
        return new AgentExecutionResult(
            Success: true,
            OutputJson: JsonSerializer.Serialize(decision),
            ErrorMessage: null,
            ExitCode: 0,
            Duration: sw.Elapsed,
            TimedOut: false,
            Cancelled: false);
    }
}
