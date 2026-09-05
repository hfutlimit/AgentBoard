using System.Reflection;
using AgentBoard.Node.WorkerOwned;
using RabbitMQ.Client;
using RabbitMQ.Client.Events;
using Xunit;

namespace AgentBoard.Node.Tests;

public sealed class WorkerRetryDeliveryTests
{
    [Fact]
    public void Deferred_work_is_confirmed_before_original_ack()
    {
        var channel = DispatchProxy.Create<IModel, ChannelProxy>();
        var proxy = (ChannelProxy)(object)channel;
        WorkerOwnedService.ReturnToTail(channel, "queue", Delivery());
        Assert.Equal(new[] { "publish", "confirm", "ack" }, proxy.Actions);
    }

    [Theory]
    [InlineData(true, false)]
    [InlineData(false, true)]
    public void Unconfirmed_or_unroutable_retry_never_acks_original(bool failure, bool returned)
    {
        var channel = DispatchProxy.Create<IModel, ChannelProxy>();
        var proxy = (ChannelProxy)(object)channel;
        proxy.Fail = failure; proxy.Returned = returned;
        Assert.Throws<IOException>(() => WorkerOwnedService.ReturnToTail(channel, "queue", Delivery()));
        Assert.DoesNotContain("ack", proxy.Actions);
    }

    private static BasicGetResult Delivery() => new(7, false, "exchange", "queue", 0,
        null!, new byte[] { 1 });

    public class ChannelProxy : DispatchProxy
    {
        public readonly List<string> Actions = [];
        public bool Fail, Returned;
        private EventHandler<BasicReturnEventArgs>? onReturn;
        protected override object? Invoke(MethodInfo? method, object?[]? args)
        {
            switch (method!.Name)
            {
                case "add_BasicReturn": onReturn += (EventHandler<BasicReturnEventArgs>)args![0]!; break;
                case "remove_BasicReturn": onReturn -= (EventHandler<BasicReturnEventArgs>)args![0]!; break;
                case "BasicPublish": Actions.Add("publish"); if (Returned) onReturn?.Invoke(this, new()); break;
                case "WaitForConfirmsOrDie": Actions.Add("confirm"); if (Fail) throw new IOException("confirm failed"); break;
                case "BasicAck": Actions.Add("ack"); break;
                default: throw new NotSupportedException(method.Name);
            }
            return null;
        }
    }
}
