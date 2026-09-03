// SPDX-License-Identifier: MIT
using System.Threading.Channels;
using Microsoft.Extensions.Options;

namespace AgentBoard.Node.Execution;

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

    public ExecutionChannel(IOptions<NodeOptions> options)
    {
        // FullMode = DropWrite (was Wait). The channel carries only a
        // wake-signal sentinel in the round-7 DB-first architecture —
        // dropping a wake is harmless because the Dispatcher's
        // periodic 2 s timer and its DB poll will pick up the work
        // anyway. The previous Wait mode would block the
        // RabbitMQ consumer's WriteAsync (and thus the BasicAck)
        // when the Dispatcher was busy, which applied broker-level
        // backpressure but also meant a slow Dispatcher could
        // stall the consumer thread for the full WriteAsync
        // duration. With DropWrite the consumer never blocks on
        // the channel; the bounded buffer still serves as a soft
        // "is the dispatcher keeping up?" hint, but it is no
        // longer in the critical path. BoundedChannelOptions
        // still caps the in-memory queue at
        // DispatchChannelCapacity, so a runaway producer cannot
        // leak memory even if the Dispatcher is dead.
        _channel = Channel.CreateBounded<WakeSignal>(new BoundedChannelOptions(Math.Max(1, options.Value.DispatchChannelCapacity))
        {
            FullMode = BoundedChannelFullMode.DropWrite,
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
