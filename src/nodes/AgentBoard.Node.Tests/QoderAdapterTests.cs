using AgentBoard.Node.Agents;
using AgentBoard.Node.Process;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Xunit;

namespace AgentBoard.Node.Tests;

public sealed class QoderAdapterTests
{
    [Fact]
    public async Task Qoder_adapter_uses_headless_print_flags_stdin_prompt_and_model()
    {
        var executor = new RecordingExecutor();
        var options = new AgentsOptions
        {
            Qoder = new AgentOptions
            {
                Command = Environment.ProcessPath!,
                WorkingDirectory = "E:\\Projects\\AgentBoard",
                TimeoutMinutes = 7,
                MaxCapturedOutputChars = 12345,
                Model = "GLM-5.3",
            },
        };
        var adapter = NewAdapter(executor, options);

        var result = await adapter.ExecuteAsync(
            new ExecutionContext(1, "proposal:42:0:qoder", "proposal", 42, 0,
                "qoder", "{}", null, WorkingDirectory: "E:\\Projects\\MappedWorkspace"), CancellationToken.None);

        Assert.True(result.Success);
        Assert.NotNull(executor.Spec);
        Assert.Equal(Environment.ProcessPath, executor.Spec!.Executable);
        Assert.Equal(
            new[]
            {
                "-p", "--output-format", "json",
                "--permission-mode", "bypass_permissions",
                "--no-session-persistence",
                "--strict-mcp-config", "--mcp-config", "{\"mcpServers\":{}}",
                "--model", "GLM-5.3",
            },
            executor.Spec.Arguments);
        Assert.Contains("Handle proposal 42", executor.Spec.StdinPayload);
        Assert.Equal("E:\\Projects\\MappedWorkspace", executor.Spec.WorkingDirectory);
        Assert.Equal(12345, executor.Spec.MaxOutputBytes);
        Assert.Equal(TimeSpan.FromMinutes(7), executor.Spec.Timeout);
    }

    [Fact]
    public async Task Qoder_adapter_fails_when_envelope_reports_error_despite_zero_exit()
    {
        var executor = new RecordingExecutor(
            """{"type":"result","subtype":"error_during_execution","is_error":true,"num_turns":3,"result":"tool crashed"}""");
        var adapter = NewAdapter(executor, new AgentsOptions
        {
            Qoder = new AgentOptions { Command = Environment.ProcessPath! },
        });

        var result = await adapter.ExecuteAsync(
            new ExecutionContext(1, "t", WorkloadTypes.Task, 1, 0, "qoder", "{}", "dev"),
            CancellationToken.None);

        Assert.False(result.Success);
    }

    [Fact]
    public async Task Qoder_adapter_forwards_configured_api_key_env_to_cli()
    {
        var name = "QODER_TEST_PAT_" + Guid.NewGuid().ToString("N")[..8];
        Environment.SetEnvironmentVariable(name, "pt-SyntheticProbeToken0123456789");
        try
        {
            var executor = new RecordingExecutor();
            var adapter = NewAdapter(executor, new AgentsOptions
            {
                Qoder = new AgentOptions { Command = Environment.ProcessPath!, ApiKeyEnv = name },
            });

            await adapter.ExecuteAsync(
                new ExecutionContext(1, "t", WorkloadTypes.Task, 1, 0, "qoder", "{}", "dev"),
                CancellationToken.None);

            Assert.Equal("pt-SyntheticProbeToken0123456789", executor.Spec!.Environment[name]);
        }
        finally
        {
            Environment.SetEnvironmentVariable(name, null);
        }
    }

    [Fact]
    public async Task Qoder_adapter_extracts_business_json_from_real_result_envelope()
    {
        // Envelope captured from qoderclicn 1.1.86 headless run.
        var executor = new RecordingExecutor(
            """{"type":"result","subtype":"success","duration_ms":2831,"is_error":false,"num_turns":2,"result":"{\"result_status\":\"succeeded\",\"summary\":\"probe write done\"}","stop_reason":"end_turn","total_credits":0.8,"usage":{"input_tokens":0,"iterations":[]},"permission_denials":[],"session_id":"db828667-5f7b-47e8-8949-e5998638766d"}""");
        var adapter = NewAdapter(executor, new AgentsOptions
        {
            Qoder = new AgentOptions { Command = Environment.ProcessPath! },
        });

        var result = await adapter.ExecuteAsync(
            new ExecutionContext(1, "t", WorkloadTypes.Task, 1, 0, "qoder", "{}", "dev"),
            CancellationToken.None);

        Assert.True(result.Success);
        Assert.Equal("""{"result_status":"succeeded","summary":"probe write done"}""", result.OutputJson);
    }

    [Fact]
    public async Task Qoder_adapter_honors_configured_arguments()
    {
        var executor = new RecordingExecutor();
        var adapter = NewAdapter(executor, new AgentsOptions
        {
            Qoder = new AgentOptions
            {
                Command = Environment.ProcessPath!,
                Arguments = ["-p", "--output-format", "text"],
            },
        });

        await adapter.ExecuteAsync(
            new ExecutionContext(1, "t", WorkloadTypes.Task, 1, 0, "qoder", "{}", "dev"),
            CancellationToken.None);

        Assert.Equal(new[] { "-p", "--output-format", "text" }, executor.Spec!.Arguments);
    }

    [Fact]
    public async Task Qoder_adapter_forwards_custom_config_dir()
    {
        const string name = "QODERCN_CONFIG_DIR";
        var original = Environment.GetEnvironmentVariable(name);
        Environment.SetEnvironmentVariable(name, "D:\\qoder-alt-root");
        try
        {
            var executor = new RecordingExecutor();
            var adapter = NewAdapter(executor, new AgentsOptions
            {
                Qoder = new AgentOptions { Command = Environment.ProcessPath! },
            });

            await adapter.ExecuteAsync(
                new ExecutionContext(1, "t", WorkloadTypes.Task, 1, 0, "qoder", "{}", "dev"),
                CancellationToken.None);

            Assert.Equal("D:\\qoder-alt-root", executor.Spec!.Environment[name]);
        }
        finally
        {
            Environment.SetEnvironmentVariable(name, original);
        }
    }

    // Boundary guard: passes by construction because ProcessExecutor clears the
    // parent environment and the adapter only forwards an allow-list. It exists
    // to fail loudly if a future change copies the parent env wholesale.
    [Fact]
    public async Task Qoder_adapter_never_forwards_desktop_sdk_markers()
    {
        string[] markers =
        [
            "QODERCLI_RUNTIME_PACKAGING", "QODER_AGENT_SDK_ENTRYPOINT",
            "QODER_AGENT_SDK_DAEMON", "QODERCN_SESSION_TYPE",
        ];
        var originals = markers.Select(Environment.GetEnvironmentVariable).ToArray();
        foreach (var marker in markers) Environment.SetEnvironmentVariable(marker, "polluted");
        try
        {
            var executor = new RecordingExecutor();
            var adapter = NewAdapter(executor, new AgentsOptions
            {
                Qoder = new AgentOptions { Command = Environment.ProcessPath! },
            });

            await adapter.ExecuteAsync(
                new ExecutionContext(1, "t", WorkloadTypes.Task, 1, 0, "qoder", "{}", "dev"),
                CancellationToken.None);

            foreach (var marker in markers)
                Assert.False(executor.Spec!.Environment.ContainsKey(marker), $"{marker} must not reach the CLI");
        }
        finally
        {
            for (var i = 0; i < markers.Length; i++)
                Environment.SetEnvironmentVariable(markers[i], originals[i]);
        }
    }

    [Fact]
    public async Task Qoder_adapter_forwards_personal_access_token_without_api_key_env_config()
    {
        const string name = "QODERCN_PERSONAL_ACCESS_TOKEN";
        var original = Environment.GetEnvironmentVariable(name);
        Environment.SetEnvironmentVariable(name, "pt-SyntheticProbeToken0123456789");
        try
        {
            var executor = new RecordingExecutor();
            var adapter = NewAdapter(executor, new AgentsOptions
            {
                Qoder = new AgentOptions { Command = Environment.ProcessPath! },
            });

            await adapter.ExecuteAsync(
                new ExecutionContext(1, "t", WorkloadTypes.Task, 1, 0, "qoder", "{}", "dev"),
                CancellationToken.None);

            Assert.Equal("pt-SyntheticProbeToken0123456789", executor.Spec!.Environment[name]);
        }
        finally
        {
            Environment.SetEnvironmentVariable(name, original);
        }
    }

    private static QoderAdapter NewAdapter(IProcessExecutor executor, AgentsOptions options) =>
        new(executor, Options.Create(options), NullLogger<QoderAdapter>.Instance);

    private sealed class RecordingExecutor : IProcessExecutor
    {
        private readonly string _output;
        private readonly int _exitCode;
        public RecordingExecutor(string output = "{\"decision\":\"submit\"}", int exitCode = 0)
        {
            _output = output;
            _exitCode = exitCode;
        }
        public ProcessSpec? Spec { get; private set; }
        public Task<ProcessResult> ExecuteAsync(ProcessSpec spec, CancellationToken ct)
        {
            Spec = spec;
            return Task.FromResult(new ProcessResult { ExitCode = _exitCode, RedactedOutput = _output });
        }
    }
}