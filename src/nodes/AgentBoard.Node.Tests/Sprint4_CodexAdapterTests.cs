using AgentBoard.Node.Agents;
using AgentBoard.Node.Process;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Xunit;

namespace AgentBoard.Node.Tests;

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
                Command = Environment.ProcessPath!,
                WorkingDirectory = "E:\\Projects\\AgentBoard",
                TimeoutMinutes = 7,
                MaxCapturedOutputChars = 12345,
                Model = "gpt-5.6-terra",
            },
        };
        var adapter = new CodexAdapter(
            executor, Options.Create(options), Options.Create(new AgentBoardOptions()),
            NullLogger<CodexAdapter>.Instance);

        var result = await adapter.ExecuteAsync(
            new ExecutionContext(1, "proposal:42:0:codex", "proposal", 42, 0,
                "codex", "{}", null, WorkingDirectory: "E:\\Projects\\MappedWorkspace"), CancellationToken.None);

        Assert.True(result.Success);
        Assert.NotNull(executor.Spec);
        Assert.Equal(Environment.ProcessPath, executor.Spec!.Executable);
        Assert.Equal(new[] { "exec", "--json", "--model", "gpt-5.6-terra" }, executor.Spec.Arguments);
        Assert.Contains("Handle proposal 42", executor.Spec.StdinPayload);
        Assert.Equal("E:\\Projects\\MappedWorkspace", executor.Spec.WorkingDirectory);
        Assert.Equal(12345, executor.Spec.MaxOutputBytes);
        Assert.Contains("PATH", executor.Spec.Environment.Keys,
            StringComparer.OrdinalIgnoreCase);
    }

    [Fact]
    public async Task Codex_adapter_uses_configured_arguments_when_provided()
    {
        // When appsettings sets Arguments (e.g. production enables unattended mode),
        // the adapter must honor them and stop falling back to the
        // hard-coded ["exec", "--json"] default.
        var executor = new RecordingExecutor();
        var options = new AgentsOptions
        {
            Codex = new AgentOptions
            {
                Command = Environment.ProcessPath!,
                WorkingDirectory = "E:\\Projects\\AgentBoard",
                TimeoutMinutes = 7,
                MaxCapturedOutputChars = 12345,
                Arguments = new[] { "exec", "--json", "--dangerously-bypass-approvals-and-sandbox" },
            },
        };
        var adapter = new CodexAdapter(
            executor, Options.Create(options), Options.Create(new AgentBoardOptions()),
            NullLogger<CodexAdapter>.Instance);

        await adapter.ExecuteAsync(
            new ExecutionContext(1, "proposal:42:0:codex", "proposal", 42, 0,
                "codex", "{}", null), CancellationToken.None);

        Assert.Equal(
            new[] { "exec", "--json", "--dangerously-bypass-approvals-and-sandbox" },
            executor.Spec!.Arguments);
    }

    [Fact]
    public async Task Codex_adapter_extracts_business_json_from_last_agent_message_event()
    {
        var executor = new RecordingExecutor("""
            {"type":"thread.started","thread_id":"t-1"}
            {"type":"item.completed","item":{"id":"i-1","type":"agent_message","text":"{\"result_status\":\"succeeded\",\"summary\":\"implemented\"}"}}
            {"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":5}}
            """);
        var options = new AgentsOptions
        {
            Codex = new AgentOptions { Command = Environment.ProcessPath! },
        };
        var adapter = new CodexAdapter(
            executor, Options.Create(options), Options.Create(new AgentBoardOptions()),
            NullLogger<CodexAdapter>.Instance);

        var result = await adapter.ExecuteAsync(
            new ExecutionContext(1, "execution-1", WorkloadTypes.Task, 42, 1,
                "codex", "{}", "dev", DurableExecution: true), CancellationToken.None);

        Assert.Equal(
            "{\"result_status\":\"succeeded\",\"summary\":\"implemented\"}",
            result.OutputJson);
    }

    [Fact]
    public async Task Durable_prompt_uses_real_task_identity_and_leaves_state_to_server()
    {
        var executor = new RecordingExecutor();
        var options = new AgentsOptions
        {
            Codex = new AgentOptions { Command = Environment.ProcessPath! },
        };
        var adapter = new CodexAdapter(
            executor, Options.Create(options), Options.Create(new AgentBoardOptions()),
            NullLogger<CodexAdapter>.Instance);

        await adapter.ExecuteAsync(
            new ExecutionContext(1, "execution-1", WorkloadTypes.Task, 731, 1,
                "codex", "{\"title\":\"durable E2E\"}", "dev",
                WorkingDirectory: "E:\\Projects\\MappedWorkspace",
                DurableExecution: true), CancellationToken.None);

        Assert.Contains("business task 731", executor.Spec!.StdinPayload);
        Assert.Contains("task_type=dev", executor.Spec.StdinPayload);
        Assert.Contains("The Server owns Task and Workflow state", executor.Spec.StdinPayload);
        Assert.Contains("\"result_status\"", executor.Spec.StdinPayload);
        Assert.DoesNotContain("submit the task for review through MCP", executor.Spec.StdinPayload);
    }

    private sealed class RecordingExecutor : IProcessExecutor
    {
        private readonly string _output;

        public RecordingExecutor(string output = "{\"action\":\"finalize\"}") => _output = output;

        public ProcessSpec? Spec { get; private set; }

        public Task<ProcessResult> ExecuteAsync(ProcessSpec spec, CancellationToken ct)
        {
            Spec = spec;
            return Task.FromResult(new ProcessResult
            {
                ExitCode = 0,
                RedactedOutput = _output,
            });
        }
    }
}
