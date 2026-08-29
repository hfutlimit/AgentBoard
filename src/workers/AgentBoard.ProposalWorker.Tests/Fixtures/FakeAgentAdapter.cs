using System.Collections.Concurrent;
using AgentBoard.ProposalWorker.Agents;
using AgentBoard.ProposalWorker.Process;

namespace AgentBoard.ProposalWorker.Tests.Fixtures;

/// <summary>
/// Programmable mock adapter. Use the static factory helpers to set
/// behavior, then read <see cref="CallCount"/> to assert routing.
/// <see cref="CallOrder"/> records the WorkloadId of every
/// invocation in call order so FIFO-admission tests can verify
/// the dispatcher pulled rows in <c>id ASC</c> order.
/// </summary>
public sealed class FakeAgentAdapter : IAgentAdapter
{
    private readonly Func<ExecutionContext, CancellationToken, Task<AgentExecutionResult>> _behavior;

    public FakeAgentAdapter(string agentType, Func<ExecutionContext, CancellationToken, Task<AgentExecutionResult>> behavior)
    {
        AgentType = agentType;
        _behavior = behavior;
    }

    public int CallCount { get; private set; }
    public ExecutionContext? LastContext { get; private set; }
    /// <summary>WorkloadIds of each invocation, in call order. Thread-safe.</summary>
    public ConcurrentQueue<long> CallOrder { get; } = new();

    public string AgentType { get; }

    public async Task<AgentExecutionResult> ExecuteAsync(ExecutionContext context, CancellationToken ct)
    {
        CallCount++;
        LastContext = context;
        CallOrder.Enqueue(context.WorkloadId);
        return await _behavior(context, ct);
    }

    // -------- factories ----------------------------------------------------

    public static FakeAgentAdapter Success(string agentType, string outputJson = "{\"action\":\"ok\"}") =>
        new(agentType, (_, _) => Task.FromResult(new AgentExecutionResult(
            Success: true, OutputJson: outputJson, ErrorMessage: null, ExitCode: 0,
            Duration: TimeSpan.FromMilliseconds(10))));

    public static FakeAgentAdapter Failure(string agentType, string error = "boom") =>
        new(agentType, (_, _) => Task.FromResult(new AgentExecutionResult(
            Success: false, OutputJson: null, ErrorMessage: error, ExitCode: 1,
            Duration: TimeSpan.FromMilliseconds(10))));

    public static FakeAgentAdapter TimedOut(string agentType) =>
        new(agentType, (_, _) => Task.FromResult(new AgentExecutionResult(
            Success: false, OutputJson: "", ErrorMessage: "timeout", ExitCode: -1,
            Duration: TimeSpan.FromSeconds(30), TimedOut: true)));

    public static FakeAgentAdapter Cancelled(string agentType) =>
        new(agentType, (_, _) => Task.FromResult(new AgentExecutionResult(
            Success: false, OutputJson: "", ErrorMessage: "cancelled", ExitCode: -1,
            Duration: TimeSpan.Zero, Cancelled: true)));

    public static FakeAgentAdapter Throws(string agentType, Exception ex) =>
        new(agentType, (_, _) => throw ex);

    public static FakeAgentAdapter Slow(string agentType, TimeSpan delay) =>
        new(agentType, async (_, ct) =>
        {
            await Task.Delay(delay, ct);
            return new AgentExecutionResult(Success: true, OutputJson: "{\"action\":\"ok\"}", ErrorMessage: null, ExitCode: 0, Duration: delay);
        });
}
