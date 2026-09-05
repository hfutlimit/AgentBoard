using AgentBoard.Node.WorkerOwned;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Xunit;

namespace AgentBoard.Node.Tests;

public sealed class LocalWorkerRuntimeTests : IDisposable
{
    private readonly string directory = Path.Combine(Path.GetTempPath(), "worker-runtime-" + Guid.NewGuid().ToString("N"));
    private readonly LocalConfigurationStore store;
    private readonly FakeFactory factory = new();
    private readonly LocalWorkerRuntime runtime;
    public LocalWorkerRuntimeTests()
    {
        Directory.CreateDirectory(directory);
        store = new LocalConfigurationStore(Path.Combine(directory, "config.json"), new WorkerOwnedOptions
        {
            Enabled = true, Projects = [new() { ProjectId = 3, LocalPath = directory }],
            Agents = [new() { Id = "a", Provider = "codex", WorkKinds = ["proposal"], Runtime = new() { Command = "codex" } }]
        });
        store.Save(store.Read());
        runtime = CreateRuntime(factory);
    }
    private LocalWorkerRuntime CreateRuntime(FakeFactory f)
    {
        var options = Options.Create(new NodeOptions { Id = "test" });
        return new(store, f, new WorkerState(options, new WorkerIdentity(options)), NullLogger<LocalWorkerRuntime>.Instance);
    }

    [Fact]
    public async Task Portal_stays_stopped_until_explicit_start_and_repeated_start_is_idempotent()
    {
        await runtime.StartAsync(default);
        Assert.Equal("stopped", (await runtime.StatusAsync()).State);
        await Task.WhenAll(runtime.StartExecutionAsync(), runtime.StartExecutionAsync());
        Assert.Single(factory.Runs);
        Assert.Equal("starting", (await runtime.StatusAsync()).State);
        factory.Runs[0].Ready = true;
        Assert.Equal("running", (await runtime.StatusAsync()).State);
    }

    [Fact]
    public async Task Stop_drains_without_cancelling_current_work_and_restart_loads_new_revision()
    {
        var before = await runtime.StartExecutionAsync();
        var edit = store.Read(); edit.Configuration.Agents[0].PrePrompt = "new"; store.Save(edit);
        Assert.True((await runtime.StatusAsync()).ConfigurationChanged);
        Assert.Equal("", factory.Configurations[0].Agents[0].PrePrompt);
        Assert.Equal("stopping", (await runtime.StopExecutionAsync()).State);
        Assert.True(factory.Runs[0].Draining);
        Assert.False(factory.Runs[0].Cancelled);
        await runtime.StartExecutionAsync(); Assert.Single(factory.Runs);
        factory.Runs[0].Finished.SetResult();
        Assert.Equal("stopped", (await runtime.StatusAsync()).State);
        var after = await runtime.StartExecutionAsync();
        Assert.NotEqual(before.LoadedRevision, after.LoadedRevision);
        Assert.Equal("new", factory.Configurations[1].Agents[0].PrePrompt);
        Assert.False(after.ConfigurationChanged);
    }

    [Fact]
    public async Task Same_configuration_cannot_run_in_two_hosts_and_lock_is_released_after_stop()
    {
        await runtime.StartExecutionAsync();
        using var other = CreateRuntime(new FakeFactory());
        await Assert.ThrowsAsync<InvalidOperationException>(() => other.StartExecutionAsync());
        await runtime.StopExecutionAsync(); factory.Runs[0].Finished.SetResult();
        await runtime.StatusAsync();
        Assert.Equal("starting", (await other.StartExecutionAsync()).State);
    }

    [Fact]
    public async Task Failed_start_is_visible_sanitized_and_can_retry()
    {
        await runtime.StartExecutionAsync();
        factory.Runs[0].Finished.SetException(new IOException("secret-password"));
        var status = await runtime.StatusAsync();
        Assert.Equal("failed", status.State); Assert.DoesNotContain("secret-password", status.Error!);
        await runtime.StartExecutionAsync(); Assert.Equal(2, factory.Runs.Count);
    }

    [Fact]
    public async Task Disabled_or_legacy_configuration_never_starts_consumers()
    {
        var edit = store.Read(); edit.Configuration.Enabled = false; store.Save(edit);
        await Assert.ThrowsAsync<InvalidOperationException>(() => runtime.StartExecutionAsync());
        edit = store.Read(); edit.Configuration.Enabled = true; store.Save(edit);
        runtime.CanControl = false;
        await Assert.ThrowsAsync<InvalidOperationException>(() => runtime.StartExecutionAsync());
        Assert.Empty(factory.Runs);
    }

    [Fact]
    public async Task Auto_start_failure_keeps_portal_available()
    {
        var edit = store.Read(); edit.Configuration.Enabled = false; store.Save(edit);
        runtime.AutoStart = true;
        await runtime.StartAsync(default);
        Assert.Equal("failed", (await runtime.StatusAsync()).State);
    }

    public void Dispose() { runtime.Dispose(); Directory.Delete(directory, true); }
    private sealed class FakeFactory : ILocalWorkerFactory
    {
        public List<FakeRun> Runs { get; } = [];
        public List<WorkerOwnedOptions> Configurations { get; } = [];
        public ILocalWorkerRun Create(WorkerOwnedOptions configuration)
        { Configurations.Add(configuration); var run = new FakeRun(); Runs.Add(run); return run; }
    }
    private sealed class FakeRun : ILocalWorkerRun
    {
        public TaskCompletionSource Finished { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public Task Completion => Finished.Task;
        public bool Ready, Draining, Cancelled;
        public bool BrokerConnected => Ready;
        public DateTimeOffset? LastScanAt => Ready ? DateTimeOffset.UtcNow : null;
        public Task StartAsync(CancellationToken ct) => Task.CompletedTask;
        public Task StopAsync(CancellationToken ct) { Cancelled = true; Finished.TrySetResult(); return Task.CompletedTask; }
        public void Drain() => Draining = true;
        public void Dispose() { }
    }
}
