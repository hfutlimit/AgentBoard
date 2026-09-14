using AgentBoard.Node.Agents;
using AgentBoard.Node.Process;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Xunit;

namespace AgentBoard.Node.Tests;

public sealed class CursorAdapterTests
{
    [Fact]
    public async Task Cursor_adapter_uses_headless_print_flags_stdin_prompt_and_model()
    {
        var executor = new RecordingExecutor();
        var options = new AgentsOptions
        {
            Cursor = new AgentOptions
            {
                Command = Environment.ProcessPath!,
                WorkingDirectory = "E:\\Projects\\AgentBoard",
                TimeoutMinutes = 7,
                MaxCapturedOutputChars = 12345,
                Model = "cursor-grok-4.6-high",
            },
        };
        var adapter = new CursorAdapter(
            executor, Options.Create(options), Options.Create(new AgentBoardOptions()),
            NullLogger<CursorAdapter>.Instance);

        var result = await adapter.ExecuteAsync(
            new ExecutionContext(1, "proposal:42:0:cursor", "proposal", 42, 0,
                "cursor", "{}", null, WorkingDirectory: "E:\\Projects\\MappedWorkspace"), CancellationToken.None);

        Assert.True(result.Success);
        Assert.NotNull(executor.Spec);
        Assert.Equal(Environment.ProcessPath, executor.Spec!.Executable);
        Assert.Equal(
            new[] { "-p", "--force", "--trust", "--approve-mcps", "--output-format", "json", "--model", "cursor-grok-4.6-high" },
            executor.Spec.Arguments);
        Assert.Contains("Handle proposal 42", executor.Spec.StdinPayload);
        Assert.Equal("E:\\Projects\\MappedWorkspace", executor.Spec.WorkingDirectory);
        Assert.Equal(12345, executor.Spec.MaxOutputBytes);
    }

    [Fact]
    public async Task Cursor_adapter_honors_configured_arguments()
    {
        var executor = new RecordingExecutor();
        var options = new AgentsOptions
        {
            Cursor = new AgentOptions
            {
                Command = Environment.ProcessPath!,
                Arguments = ["-p", "--force", "--output-format", "text"],
            },
        };
        var adapter = new CursorAdapter(
            executor, Options.Create(options), Options.Create(new AgentBoardOptions()),
            NullLogger<CursorAdapter>.Instance);

        await adapter.ExecuteAsync(
            new ExecutionContext(1, "proposal:42:0:cursor", "proposal", 42, 0,
                "cursor", "{}", null), CancellationToken.None);

        Assert.Equal(new[] { "-p", "--force", "--output-format", "text" }, executor.Spec!.Arguments);
    }

    [Fact]
    public async Task Cursor_adapter_extracts_business_json_from_result_envelope()
    {
        var executor = new RecordingExecutor("""
            {"type":"result","result":"{\"decision\":\"submit\",\"summary\":\"done\"}"}
            """);
        var options = new AgentsOptions
        {
            Cursor = new AgentOptions { Command = Environment.ProcessPath! },
        };
        var adapter = new CursorAdapter(
            executor, Options.Create(options), Options.Create(new AgentBoardOptions()),
            NullLogger<CursorAdapter>.Instance);

        var result = await adapter.ExecuteAsync(
            new ExecutionContext(1, "execution-1", WorkloadTypes.Task, 42, 1,
                "cursor", "{}", "dev", DurableExecution: true), CancellationToken.None);

        Assert.Equal("{\"decision\":\"submit\",\"summary\":\"done\"}", result.OutputJson);
    }

    [Fact]
    public async Task Cursor_adapter_injects_per_agent_identity_into_cli_environment()
    {
        var executor = new RecordingExecutor();
        var options = new AgentsOptions
        {
            Cursor = new AgentOptions
            {
                Command = Environment.ProcessPath!,
                AgentBoardToken = "tok-cursor-per-agent",
            },
        };
        var agentboard = new AgentBoardOptions
        {
            ServerUrl = "http://127.0.0.1:58124",
            StartupToken = "tok-startup",
        };
        var adapter = new CursorAdapter(
            executor, Options.Create(options), Options.Create(agentboard),
            NullLogger<CursorAdapter>.Instance);

        await adapter.ExecuteAsync(
            new ExecutionContext(1, "t", WorkloadTypes.Task, 1, 0, "cursor", "{}", "dev"),
            CancellationToken.None);

        Assert.Equal("tok-cursor-per-agent", executor.Spec!.Environment["AGENTBOARD_MCP_TOKEN"]);
        Assert.Equal("http://127.0.0.1:58124", executor.Spec.Environment["AGENTBOARD_API_URL"]);
    }

    private sealed class RecordingExecutor : IProcessExecutor
    {
        private readonly string _output;
        public RecordingExecutor(string output = "{\"decision\":\"submit\"}") => _output = output;
        public ProcessSpec? Spec { get; private set; }
        public Task<ProcessResult> ExecuteAsync(ProcessSpec spec, CancellationToken ct)
        {
            Spec = spec;
            return Task.FromResult(new ProcessResult { ExitCode = 0, RedactedOutput = _output });
        }
    }
}
