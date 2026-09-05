using AgentBoard.Node.Agents;
using Microsoft.Extensions.Options;

namespace AgentBoard.Node.WorkerOwned;

public interface ILocalWorkerRun : IDisposable
{
    Task? Completion { get; }
    bool BrokerConnected { get; }
    DateTimeOffset? LastScanAt { get; }
    Task StartAsync(CancellationToken ct);
    Task StopAsync(CancellationToken ct);
    void Drain();
}

public interface ILocalWorkerFactory
{
    ILocalWorkerRun Create(WorkerOwnedOptions configuration);
}

public sealed class LocalWorkerFactory(IServiceProvider services) : ILocalWorkerFactory
{
    public ILocalWorkerRun Create(WorkerOwnedOptions configuration) =>
        ActivatorUtilities.CreateInstance<WorkerOwnedService>(services, Options.Create(configuration));
}

public sealed record LocalRuntimeStatus(string State, string? LoadedRevision, bool ConfigurationChanged,
    bool BrokerConnected, DateTimeOffset? LastScanAt, int ActiveCount, string? Error);

/// <summary>The portal owns a restartable worker lifetime, independently of the HTTP host.</summary>
public sealed class LocalWorkerRuntime(LocalConfigurationStore store, ILocalWorkerFactory factory,
    WorkerState state, ILogger<LocalWorkerRuntime> log) : IHostedService, IDisposable
{
    private readonly SemaphoreSlim gate = new(1, 1);
    private ILocalWorkerRun? run;
    private FileStream? processLock;
    private string? revision;
    private bool stopping;
    private string? error;
    public bool AutoStart { get; set; }
    public bool CanControl { get; set; } = true;

    public async Task<LocalRuntimeStatus> StatusAsync(CancellationToken ct = default)
    {
        await gate.WaitAsync(ct);
        try { Reap(); return Status(); }
        finally { gate.Release(); }
    }

    public async Task<LocalRuntimeStatus> StartExecutionAsync(CancellationToken ct = default)
    {
        await gate.WaitAsync(ct);
        try
        {
            Reap();
            if (!CanControl) throw new InvalidOperationException("当前运行旧版执行模式，请使用本机配置入口启动，不能同时开启两套执行程序。");
            if (run is not null) return Status(); // Repeated clicks cannot create duplicate consumers.
            error = null;
            var snapshot = store.Read();
            snapshot.Configuration.Validate();
            if (!snapshot.Configuration.Enabled)
                throw new InvalidOperationException("请先启用 Worker 并保存配置。");
            if (!snapshot.Configuration.EnabledAgents.Any())
                throw new InvalidOperationException("请至少启用一个 Agent 并选择工作类型。");
            Directory.CreateDirectory(Path.GetDirectoryName(store.FilePath)!);
            try { processLock = new FileStream(store.FilePath + ".runtime.lock", FileMode.OpenOrCreate,
                FileAccess.ReadWrite, FileShare.None); }
            catch (IOException) { throw new InvalidOperationException("此配置已有执行 Worker 运行，请使用原页面停止后再启动。"); }
            try
            {
                run = factory.Create(snapshot.Configuration);
                revision = snapshot.Revision;
                stopping = false;
                state.Paused = false;
                state.LastError = null;
                await run.StartAsync(CancellationToken.None); // Request disconnect must not stop execution.
                Reap();
                return Status();
            }
            catch { run?.Dispose(); run = null; processLock?.Dispose(); processLock = null; throw; }
        }
        finally { gate.Release(); }
    }

    public async Task<LocalRuntimeStatus> StopExecutionAsync(CancellationToken ct = default)
    {
        await gate.WaitAsync(ct);
        try
        {
            Reap();
            if (run is not null) { stopping = true; run.Drain(); }
            return Status();
        }
        finally { gate.Release(); }
    }

    private void Reap()
    {
        if (run?.Completion is not { IsCompleted: true } completion) return;
        if (completion.IsFaulted)
        {
            // Never return raw exception messages: URI/parser errors may contain credentials.
            error = "执行 Worker 启动或运行失败（" + completion.Exception!.GetBaseException().GetType().Name
                + "）。请检查连接、CLI 配置和本机日志后重新启动。";
            log.LogWarning("Local Worker exited with {ErrorType}", completion.Exception.GetBaseException().GetType().Name);
        }
        run.Dispose(); run = null;
        processLock?.Dispose(); processLock = null;
        stopping = false;
    }

    private LocalRuntimeStatus Status() => new(
        run is null ? (error is null ? "stopped" : "failed") : stopping ? "stopping"
            : !run.BrokerConnected || run.LastScanAt is null
                || DateTimeOffset.UtcNow - run.LastScanAt > TimeSpan.FromSeconds(60) ? "starting"
            : state.Paused ? "paused"
            : state.ActiveCount > 0 ? "busy" : "running",
        revision, run is not null && revision != store.Read().Revision,
        run?.BrokerConnected ?? false, run?.LastScanAt, state.ActiveCount, error);

    public async Task StartAsync(CancellationToken ct)
    {
        if (!AutoStart) return;
        try { await StartExecutionAsync(ct); }
        catch (Exception e)
        {
            error = "自动启动失败（" + e.GetType().Name + "）。请检查已保存配置后在页面重新启动。";
            log.LogWarning("Local Worker automatic start failed with {ErrorType}", e.GetType().Name);
        }
    }

    public async Task StopAsync(CancellationToken ct)
    {
        await gate.WaitAsync(ct);
        try { if (run is not null) await run.StopAsync(ct); }
        finally { gate.Release(); }
    }

    public void Dispose() { run?.Dispose(); processLock?.Dispose(); gate.Dispose(); }
}
