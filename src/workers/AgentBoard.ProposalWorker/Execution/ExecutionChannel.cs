// SPDX-License-Identifier: MIT
using System.Threading.Channels;
using Microsoft.Extensions.Options;

namespace AgentBoard.ProposalWorker.Execution;

/// <summary>
/// Sprint 3. Bounded <see cref="Channel{T}"/> between consumer and
/// dispatcher. Carries only a <see cref="WakeSignal"/> sentinel — the
/// actual work payload lives in the durable DB inbox
/// (<c>worker_execution_inbox</c>) and the Dispatcher reads it
/// directly. This is the DB-first scheduling architecture the
/// 2026-08-29 review follow-up converged on: the channel is no
/// longer the work queue, it is a wake-signal. Carrying the full
/// <see cref="ExecutionRequest"/> in the channel is what made the
/// previous design starve DB-only pending rows under sustained
/// RabbitMQ load — the bounded channel could stay permanently
/// full, the inner drain loop never returned to the outer
/// "refill from DB" step, and DB-only rows sat forever.
///
/// BoundedChannelFullMode.Wait still gives RabbitMQ backpressure:
/// if the Dispatcher stops reading the wake-signal queue (e.g.
/// degraded, paused), the producer (RabbitMQ consumer) blocks on
/// WriteAsync, which in turn blocks the BasicAck, which in turn
/// applies AMQP-level backpressure to the broker. Capacity is
/// not a meaningful bottleneck here (the wake-signal is a single
/// <see cref="long"/> timestamp) but the bounded-channel contract
/// preserves the same backpressure semantics.
/// </summary>
public sealed class ExecutionChannel
{
    private readonly Channel<WakeSignal> _channel;

    public ExecutionChannel(IOptions<WorkerOptions> options)
    {
        _channel = Channel.CreateBounded<WakeSignal>(new BoundedChannelOptions(Math.Max(1, options.Value.DispatchChannelCapacity))
        {
            FullMode = BoundedChannelFullMode.Wait,
            SingleReader = true,
            SingleWriter = false,
        });
    }

    public ChannelWriter<WakeSignal> Writer => _channel.Writer;
    public ChannelReader<WakeSignal> Reader => _channel.Reader;

    public void Complete() => _channel.Writer.TryComplete();
}

/// <summary>
/// Sentinel written to the <see cref="ExecutionChannel"/> when a new
/// row appears in the durable DB inbox. The Dispatcher wakes on
/// any signal (or the periodic timer) and queries the DB directly
/// for the oldest pending row. The signal carries no work
/// payload — the actual (request, inboxId) pair is read from
/// <c>worker_execution_inbox</c> at dispatch time.
/// </summary>
public readonly struct WakeSignal
{
    /// <summary>UTC timestamp the signal was emitted (for logging / debugging).</summary>
    public DateTimeOffset At { get; init; }
    /// <summary>Logical source label, e.g. "rabbit", "refill", "test", "manual".</summary>
    public string Source { get; init; }
}
