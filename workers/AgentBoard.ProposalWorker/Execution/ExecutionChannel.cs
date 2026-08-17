using System.Threading.Channels;
using Microsoft.Extensions.Options;

namespace AgentBoard.ProposalWorker.Execution;

/// <summary>
/// Sprint 3. Bounded <see cref="Channel{T}"/> between consumer and dispatcher.
/// Carries (request, inboxId) so the dispatcher knows which inbox row to
/// claim. Capacity from config (default 100). FullMode=Wait so producers
/// block when the worker is overloaded — RabbitMQ backpressure surfaces
/// naturally because the consumer can't ACK until WriteAsync returns.
/// </summary>
public sealed class ExecutionChannel
{
    private readonly Channel<InFlightExecution> _channel;

    public ExecutionChannel(IOptions<WorkerOptions> options)
    {
        _channel = Channel.CreateBounded<InFlightExecution>(new BoundedChannelOptions(Math.Max(1, options.Value.DispatchChannelCapacity))
        {
            FullMode = BoundedChannelFullMode.Wait,
            SingleReader = true,
            SingleWriter = false,
        });
    }

    public ChannelWriter<InFlightExecution> Writer => _channel.Writer;
    public ChannelReader<InFlightExecution> Reader => _channel.Reader;

    public void Complete() => _channel.Writer.TryComplete();
}

public sealed record InFlightExecution(ExecutionRequest Request, long InboxId);
