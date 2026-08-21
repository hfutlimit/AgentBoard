using System.Text.Json;
using Microsoft.Extensions.Options;

namespace AgentBoard.ProposalWorker.Agents;

/// <summary>
/// In-process stand-in for a real agent CLI. Used when no external CLI is
/// installed locally or when the user wants to exercise the worker pipeline
/// end-to-end without invoking a model. Echoes the proposal id / round and
/// returns a synthetic <c>ask</c> decision that the coordinator will route
/// back through MCP, so downstream assertions still fire.
/// </summary>
public sealed class FakeAdapter : IAgentAdapter
{
    private readonly AgentsOptions _agents;
    private readonly ILogger<FakeAdapter> _log;

    public FakeAdapter(IOptions<AgentsOptions> agents, ILogger<FakeAdapter> log)
    {
        _agents = agents.Value;
        _log = log;
    }

    public string AgentType => "fake";

    public async Task<AgentExecutionResult> ExecuteAsync(ExecutionContext context, CancellationToken ct)
    {
        var started = DateTimeOffset.UtcNow;
        _log.LogInformation(
            "fake agent executing workload {WorkloadId} round {Round} on worker key {ExecutionKey}",
            context.WorkloadId, context.Round, context.ExecutionKey);

        // Yield once so the dispatcher is forced to treat this like a real
        // async operation; without this the call resolves synchronously and
        // stress tests of the channel/heartbeat can race.
        await Task.Yield();

        var decision = new
        {
            action = "ask",
            reason = "fake agent always asks for clarification",
            open_questions = new[]
            {
                new
                {
                    id = "fake-q1",
                    prompt = "fake agent placeholder: confirm scope before proceeding",
                    options = new[] { "yes", "no" },
                    multi = false,
                },
            },
            proposal_id = context.WorkloadId,
            round = context.Round,
            worker_key = context.ExecutionKey,
        };
        var json = JsonSerializer.Serialize(decision);

        return new AgentExecutionResult(
            Success: true,
            OutputJson: json,
            ErrorMessage: null,
            ExitCode: 0,
            Duration: DateTimeOffset.UtcNow - started);
    }
}
