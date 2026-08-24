using AgentBoard.ProposalWorker.Process;
using Microsoft.Extensions.Options;

namespace AgentBoard.ProposalWorker.Agents;

/// <summary>
/// Sprint 4. OpenAI Codex CLI adapter. Codex uses argv-style input
/// (<c>codex exec --prompt &lt;text&gt;</c>) instead of stdin. We pass the
/// prompt as a single argument so the existing <c>codex</c> CLI is the only
/// dependency; no stdin plumbing required.
/// </summary>
public sealed class CodexAdapter : IAgentAdapter
{
    private readonly IProcessExecutor _process;
    private readonly AgentsOptions _agents;

    public CodexAdapter(IProcessExecutor process, IOptions<AgentsOptions> agents)
    {
        _process = process;
        _agents = agents.Value;
    }

    public string AgentType => "codex";

    public Task<AgentExecutionResult> ExecuteAsync(ExecutionContext context, CancellationToken ct)
    {
        var opts = _agents.Codex;
        var prompt = BuildPrompt(context);
        var spec = new ProcessSpec
        {
            Executable = opts.Command,
            WorkingDirectory = opts.WorkingDirectory,
            // codex expects: codex exec --prompt <text>
            Arguments = new[] { "exec", "--prompt", prompt },
            Timeout = TimeSpan.FromMinutes(Math.Max(1, opts.TimeoutMinutes)),
            MaxOutputBytes = opts.MaxCapturedOutputChars,
            AgentType = AgentType,
        };
        return SharedAdapterHelpers.RunAndParseAsync(_process, spec, ct);
    }

    private string BuildPrompt(ExecutionContext context) => $"""
        You are the AgentBoard worker running on OpenAI Codex CLI. Use your configured AgentBoard MCP only.
        Handle proposal {context.WorkloadId} (round {context.Round}) on worker '{context.ExecutionKey}'.
        Reconstruct the proposal's complete question-answer history through MCP, then decide the next action.
        If you need clarification, write concrete open questions through MCP. If converged, write the converged proposal. If appropriate, record failure.
        Unattended mode: do not make destructive local changes unless the proposal explicitly asks and MCP confirms scope.
        """;
}
