using AgentBoard.Node.WorkerOwned;
using AgentBoard.Node.Agents;
using AgentBoard.Node.Process;
using Microsoft.Extensions.Logging.Abstractions;
using Xunit;

namespace AgentBoard.Node.Tests;

public sealed class LocalConfigurationTests : IDisposable
{
    private readonly string directory = Path.Combine(Path.GetTempPath(), "worker-config-tests-" + Guid.NewGuid().ToString("N"));
    public LocalConfigurationTests() => Directory.CreateDirectory(directory);
    public void Dispose() => Directory.Delete(directory, recursive: true);

    private WorkerOwnedOptions Configuration() => new()
    {
        Enabled = true,
        Projects = [new() { ProjectId = 16, LocalPath = directory }],
        Agents = [new() { Id = "codex-a", Provider = "codex", WorkKinds = ["dev"], ProjectIds = [16],
            Runtime = new() { Command = "codex", Model = "gpt-5.6-terra" } }],
    };

    [Fact]
    public void Saved_configuration_round_trips_into_runtime_and_keeps_previous_backup()
    {
        var path = Path.Combine(directory, "config.json");
        var store = new LocalConfigurationStore(path, Configuration());
        var draft = store.Read();
        draft.Configuration.Agents[0].PrePrompt = "先读规范";
        draft.Configuration.Agents[0].Prompts["dev"] = new() { Pre = "复现缺陷", Post = "复查回归" };
        var saved = store.Save(draft);
        var reloaded = new LocalConfigurationStore(path, new()).Load();
        Assert.Contains("复现缺陷", WorkPlanner.Prompt("dev", "{}", reloaded.Agents[0]));
        Assert.Equal("先读规范", reloaded.Agents[0].PrePrompt);
        saved.Configuration.Agents[0].PostPrompt = "如实报告";
        store.Save(saved);
        Assert.True(File.Exists(path + ".bak"));
        Assert.DoesNotContain("如实报告", File.ReadAllText(path + ".bak"));
    }

    [Fact]
    public void Stale_editor_cannot_overwrite_newer_save()
    {
        var store = new LocalConfigurationStore(Path.Combine(directory, "config.json"), Configuration());
        var first = store.Read();
        var stale = store.Read();
        first.Configuration.Agents[0].PrePrompt = "new";
        store.Save(first);
        Assert.Throws<ConfigurationConflictException>(() => store.Save(stale));
        Assert.Equal("new", store.Read().Configuration.Agents[0].PrePrompt);
    }

    [Fact]
    public void Removed_agent_and_capabilities_do_not_reappear_from_default_arrays()
    {
        var defaults = Configuration();
        var second = LocalConfigurationStore.Clone(defaults).Agents[0];
        second.Id = "codex-b";
        defaults.Agents = [defaults.Agents[0], second];
        var store = new LocalConfigurationStore(Path.Combine(directory, "config.json"), defaults);
        var snapshot = store.Read();
        snapshot.Configuration.Agents = [snapshot.Configuration.Agents[0]];
        snapshot.Configuration.Agents[0].WorkKinds = ["qa"];
        store.Save(snapshot);
        var loaded = store.Load();
        Assert.Single(loaded.Agents);
        Assert.Equal(["qa"], loaded.Agents[0].WorkKinds);
        Assert.Empty(loaded.Candidates(16, "dev"));
    }

    [Fact]
    public void Disabled_agents_cannot_subscribe_or_be_selected()
    {
        var config = Configuration();
        config.Agents[0].Enabled = false;
        config.Validate();
        Assert.Empty(config.EnabledAgents);
        Assert.Empty(config.Subscriptions());
        Assert.Empty(config.Candidates(16, "dev"));
    }

    [Fact]
    public void Browser_does_not_receive_service_token_and_cannot_save_one()
    {
        var defaults = Configuration();
        defaults.Agents[0].Runtime.AgentBoardToken = "test-secret";
        var store = new LocalConfigurationStore(Path.Combine(directory, "config.json"), defaults);
        var view = store.Read();
        Assert.Empty(view.Configuration.Agents[0].Runtime.AgentBoardToken);
        Assert.Equal("test-secret", defaults.Agents[0].Runtime.AgentBoardToken);
        Assert.Throws<InvalidOperationException>(() => store.Save(view));
        view.Configuration.Agents[0].Runtime.AgentBoardToken = "not-allowed";
        Assert.Throws<InvalidOperationException>(() => store.Save(view));
        Assert.False(File.Exists(store.FilePath));
    }

    [Theory]
    [InlineData("ticket")]
    [InlineData("review")]
    [InlineData("bug")]
    public void Unknown_work_kinds_and_prompt_scopes_are_rejected(string kind)
    {
        var config = Configuration();
        config.Agents[0].Prompts[kind] = new();
        Assert.Throws<InvalidOperationException>(() => config.ValidateConfiguration());
        config.Agents[0].Prompts.Clear();
        config.Agents[0].WorkKinds = [kind];
        Assert.Throws<InvalidOperationException>(() => config.ValidateConfiguration());
    }

    [Fact]
    public void Prompt_layers_have_deterministic_order_and_no_cross_kind_leak()
    {
        var a = Configuration().Agents[0];
        a.PrePrompt = "COMMON_PRE";
        a.PostPrompt = "COMMON_POST";
        a.Prompts["dev"] = new() { Pre = "DEV_PRE", Post = "DEV_POST" };
        a.Prompts["qa"] = new() { Pre = "QA_ONLY" };
        var prompt = WorkPlanner.Prompt("dev", "TASK_CONTEXT", a);
        var markers = new[] { "COMMON_PRE", "DEV_PRE", "TASK_CONTEXT", "DEV_POST", "COMMON_POST" };
        var positions = markers.Select(m => prompt.IndexOf(m, StringComparison.Ordinal)).ToArray();
        Assert.All(positions, p => Assert.True(p >= 0));
        Assert.Equal(positions.OrderBy(p => p), positions);
        Assert.DoesNotContain("QA_ONLY", prompt);
        Assert.Contains("single JSON object", prompt);
        Assert.Contains("Do not deploy to production", prompt);
    }

    [Fact]
    public void Invalid_or_oversized_configuration_never_replaces_saved_file()
    {
        var store = new LocalConfigurationStore(Path.Combine(directory, "config.json"), Configuration());
        var saved = store.Save(store.Read());
        var before = File.ReadAllText(store.FilePath);
        saved.Configuration.Agents[0].PostPrompt = new string('x', 20001);
        Assert.Throws<InvalidOperationException>(() => store.Save(saved));
        Assert.Equal(before, File.ReadAllText(store.FilePath));
    }

    [Fact]
    public async Task Saved_prompts_reach_the_actual_codex_adapter_stdin()
    {
        var store = new LocalConfigurationStore(Path.Combine(directory, "config.json"), Configuration());
        var draft = store.Read();
        draft.Configuration.Agents[0].PrePrompt = "READ_LOCAL_GUIDANCE";
        draft.Configuration.Agents[0].PostPrompt = "VERIFY_ACTUAL_RESULTS";
        draft.Configuration.Agents[0].Runtime.Command = Environment.ProcessPath!;
        store.Save(draft);
        var profile = store.Load().Agents[0];
        var process = new CaptureProcess();
        var adapter = new LocalAdapterFactory(process, NullLoggerFactory.Instance).Create(profile);
        await adapter.ExecuteAsync(new ExecutionContext(1, "test", WorkloadTypes.Task, 42, 1,
            "codex", "{}", WorkPlanner.Prompt("dev", "{}", profile),
            WorkingDirectory: directory, WorkerOwnedExecution: true), CancellationToken.None);
        Assert.Contains("READ_LOCAL_GUIDANCE", process.Spec!.StdinPayload);
        Assert.Contains("VERIFY_ACTUAL_RESULTS", process.Spec.StdinPayload);
        Assert.Contains("Do not call AgentBoard mutation", process.Spec.StdinPayload);
    }

    [Fact]
    public void Concurrent_process_edit_lock_rejects_save()
    {
        var store = new LocalConfigurationStore(Path.Combine(directory, "config.json"), Configuration());
        using var otherProcess = new FileStream(store.FilePath + ".edit.lock", FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
        Assert.Throws<ConfigurationConflictException>(() => store.Save(store.Read()));
        Assert.False(File.Exists(store.FilePath));
    }

    private sealed class CaptureProcess : IProcessExecutor
    {
        public ProcessSpec? Spec { get; private set; }
        public Task<ProcessResult> ExecuteAsync(ProcessSpec spec, CancellationToken ct)
        {
            Spec = spec;
            return Task.FromResult(new ProcessResult { ExitCode = 0, RedactedOutput = "{\"decision\":\"submit\",\"summary\":\"test\"}" });
        }
    }
}
