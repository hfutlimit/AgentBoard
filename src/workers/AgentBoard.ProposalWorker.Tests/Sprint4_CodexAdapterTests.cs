using AgentBoard.ProposalWorker.Agents;
using AgentBoard.ProposalWorker.Process;
using Microsoft.Extensions.Options;
using Xunit;

namespace AgentBoard.ProposalWorker.Tests;

public sealed class Sprint4_CodexAdapterTests
{
    [Fact]
    public async Task Codex_adapter_uses_json_exec_and_stdin_prompt()
    {
        var executor = new RecordingExecutor();
        var options = new AgentsOptions
        {
            Codex = new AgentOptions
            {
                Command = "codex",
                WorkingDirectory = "E:\\Projects\\AgentBoard",
                TimeoutMinutes = 7,
                MaxCapturedOutputChars = 12345,
            },
        };
        var adapter = new CodexAdapter(executor, Options.Create(options));

        var result = await adapter.ExecuteAsync(
            new ExecutionContext(1, "proposal:42:0:codex", "proposal", 42, 0,
                "codex", "{}", null), CancellationToken.None);

        Assert.True(result.Success);
        Assert.NotNull(executor.Spec);
        Assert.Equal("codex", executor.Spec!.Executable);
        Assert.Equal(new[] { "exec", "--json" }, executor.Spec.Arguments);
        Assert.Contains("Handle proposal 42", executor.Spec.StdinPayload);
        Assert.Equal("E:\\Projects\\AgentBoard", executor.Spec.WorkingDirectory);
        Assert.Equal(12345, executor.Spec.MaxOutputBytes);
        Assert.Contains("PATH", executor.Spec.Environment.Keys,
            StringComparer.OrdinalIgnoreCase);
    }

    private sealed class RecordingExecutor : IProcessExecutor
    {
        public ProcessSpec? Spec { get; private set; }

        public Task<ProcessResult> ExecuteAsync(ProcessSpec spec, CancellationToken ct)
        {
            Spec = spec;
            return Task.FromResult(new ProcessResult
            {
                ExitCode = 0,
                RedactedOutput = "{\"action\":\"finalize\"}",
            });
        }
    }
}
