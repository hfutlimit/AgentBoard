using AgentBoard.Node;
using AgentBoard.Node.Agents;
using AgentBoard.Node.Execution;
using AgentBoard.Node.Durable;
using AgentBoard.Node.Platform;
using AgentBoard.Node.Process;
using AgentBoard.Node.WorkerOwned;
using Microsoft.Extensions.Options;

// Lock the content root to the directory the executable lives in. Without
// this, a Windows service started by sc.exe runs with CWD = C:\Windows\
// system32, so the host never finds appsettings.json / appsettings.Produc
// tion.json beside the binary. Same protection when launched manually
// from an unrelated directory.
try
{
    var exeDir = AppContext.BaseDirectory;
    if (!string.IsNullOrWhiteSpace(exeDir) && Directory.Exists(exeDir))
    {
        Directory.SetCurrentDirectory(exeDir);
    }
}
catch { /* best-effort; production still boots from default location */ }

var builder = WebApplication.CreateBuilder(args);
var configurationOnly = builder.Configuration.GetValue<bool>("Portal:ConfigurationOnly");
var localDefaults = builder.Configuration.GetSection("WorkerOwned").Get<WorkerOwnedOptions>() ?? new();
var localConfiguration = new LocalConfigurationStore(
    builder.Configuration["LocalConfigurationPath"] ?? "data/worker-owned.local.json", localDefaults);
var localSnapshot = localConfiguration.Load();
builder.Host.UseWindowsService(options => options.ServiceName = "AgentBoard Node");
builder.WebHost.UseUrls(builder.Configuration["Portal:Urls"] ?? "http://127.0.0.1:58240");

// ---- Options ---------------------------------------------------------------
// P7b: ``Node`` is the canonical configuration section, ``Worker`` is the
// legacy alias kept for one release. BOTH are bound - ``Worker`` first as the
// baseline, then ``Node`` overriding only the keys that are actually PRESENT
// in the Node section and non-empty.
//
// Two earlier attempts were wrong and are worth recording:
//   1. ``if (!node.Exists()) use worker`` - appsettings.json ships defaults,
//      so a section-level fallback is dead code and the legacy section is
//      ignored entirely (worker_id stays empty, registration + /health break).
//   2. "copy every non-empty property of a Node-bound scratch object" - the
//      scratch object carries constructor defaults, so a Node section that
//      merely omits a key would push the default (HeartbeatSeconds 15, the
//      shipped db path, concurrency 1) over an operator's legacy values.
// What works is per-key presence: only keys the Node section really contains
// may win. That is also why appsettings.json deliberately ships ONLY the
// ``Worker`` section - a ``Node`` section there would list every default key
// and, under the "present wins" rule, drown out the legacy values.
builder.Services.Configure<NodeOptions>(options =>
{
    builder.Configuration.GetSection("Worker").Bind(options);
    NodeOptions.BindNonEmpty(builder.Configuration.GetSection("Node"), options);
});
builder.Services.Configure<RabbitMqOptions>(builder.Configuration.GetSection("RabbitMq"));
builder.Services.Configure<AgentsOptions>(builder.Configuration.GetSection("Agents"));
builder.Services.Configure<AgentBoardOptions>(builder.Configuration.GetSection("AgentBoard"));
builder.Services.Configure<PortalOptions>(builder.Configuration.GetSection("Portal"));
builder.Services.Configure<ProcessExecutorOptions>(builder.Configuration.GetSection("ProcessExecutor"));
builder.Services.Configure<DurableExecutionOptions>(builder.Configuration.GetSection("DurableExecution"));
builder.Services.AddSingleton<IOptions<WorkerOwnedOptions>>(Options.Create(localSnapshot));
var workerOwned = localSnapshot.Enabled;
// An explicitly saved but disabled Worker-owned configuration must never
// fall back to the legacy broad consumers on restart.
var localMode = workerOwned || File.Exists(localConfiguration.FilePath);
if (workerOwned && builder.Configuration.GetValue<bool>("DurableExecution:Enabled"))
    throw new InvalidOperationException("WorkerOwned and legacy DurableExecution cannot both consume work");
builder.Services.AddSingleton<LocalAdapterFactory>();
builder.Services.AddSingleton(localConfiguration);
builder.Services.AddSingleton<ILocalWorkerFactory, LocalWorkerFactory>();
builder.Services.AddSingleton<LocalWorkerRuntime>(services =>
    new LocalWorkerRuntime(localConfiguration, services.GetRequiredService<ILocalWorkerFactory>(),
        services.GetRequiredService<WorkerState>(), services.GetRequiredService<ILogger<LocalWorkerRuntime>>())
    { AutoStart = !configurationOnly && workerOwned, CanControl = localMode || configurationOnly });
builder.Services.AddHostedService(services => services.GetRequiredService<LocalWorkerRuntime>());
builder.Services.AddSingleton<ILocalWorkspaceResolver, ConfiguredLocalWorkspaceResolver>();

// ---- M0.1 (v4.3): cross-platform abstractions -----------------------------
// Resolved once through PlatformFactory so consumers (M0.4 IPC transport,
// M1.0/M1.1 service install, M1.2 SQLite path) never branch on the host OS
// themselves. Registration is eager: an unsupported platform fails at startup
// with a named host rather than throwing later inside a background service.
builder.Services.AddSingleton<IUserIdentity>(_ => PlatformFactory.CreateUserIdentity());
builder.Services.AddSingleton<IPlatformInfo>(sp =>
    PlatformFactory.CreatePlatformInfo(sp.GetRequiredService<IUserIdentity>()));
// Session-shaped process control (M3.4 ProviderSession, M5 ACP). The batch
// path (IProcessExecutor) stays registered below for the one-shot CLI adapters.
builder.Services.AddSingleton<IProcessRunner>(_ => PlatformFactory.CreateProcessRunner());

// ---- Sprint 5: shared process layer ---------------------------------------
builder.Services.AddSingleton<IProcessExecutor, ProcessExecutor>();

// ---- Sprint 4: per-agent adapters + registry -------------------------------
// Each adapter is singleton; missing Command means the agent is not used
// (we still register so the registry logs the full list, but Get() will
// throw at runtime if a message arrives for a disabled agent).
builder.Services.AddSingleton<IAgentAdapter, WorkBuddyAdapter>();
builder.Services.AddSingleton<IAgentAdapter, MiniMaxAdapter>();
builder.Services.AddSingleton<IAgentAdapter, CodexAdapter>();
builder.Services.AddSingleton<IAgentAdapter, QwenAdapter>();
builder.Services.AddSingleton<IAgentAdapter, FakeAdapter>();
builder.Services.AddSingleton<IAgentAdapter, DeterministicScenarioAdapter>();
builder.Services.AddSingleton<IAgentAdapterRegistry, AgentAdapterRegistry>();

// ---- Sprint 1+2: storage + inbox ------------------------------------------
builder.Services.AddSingleton<ExecutionStore>();
builder.Services.AddSingleton<InboxStore>();
builder.Services.AddSingleton<ExecutionChannel>();

// ---- Sprint 4: single translation point (RabbitMQ message → request) ------
// 2026-09-02: same DefaultAgent injection as the WorkflowMessageMapper below,
// so proposal messages without server-set agent_type fall back to the
// operator-configured agent (Agents:DefaultAgent) instead of a hard-coded
// slot name that may not exist in the C# registry (Glm53F bug).
builder.Services.AddSingleton<ProposalMessageMapper>(sp =>
    new ProposalMessageMapper(
        sp.GetRequiredService<IAgentAdapterRegistry>(),
        sp.GetRequiredService<ILogger<ProposalMessageMapper>>(),
        sp.GetRequiredService<IOptions<AgentsOptions>>().Value.DefaultAgent));
// ---- Sprint 12: workflow event translation (agentboard.workflow ns) ------
// 2026-09-01: forward the configured Agents:DefaultAgent to the mapper so
// workflow messages with agent_type=null (publisher PR-5 fix is in but
// still emits null) can fall back to a known-registered agent instead of
// being DLQ'd. Operators who want strict PR-5 enforcement can leave
// DefaultAgent unset (null).
builder.Services.AddSingleton<WorkflowMessageMapper>(sp =>
    new WorkflowMessageMapper(
        sp.GetRequiredService<IAgentAdapterRegistry>(),
        sp.GetRequiredService<IOptions<AgentsOptions>>().Value.DefaultAgent));
builder.Services.AddSingleton<ExecutionCoordinator>();

// ---- Sprint 6: worker state (must be after Process layer for snapshot) ----
builder.Services.AddSingleton<WorkerState>();

// Single resolved worker id; all consumers (state, rabbit, heartbeat) read
// from this one object so they cannot disagree (#7 in the 2026-08-28 review).
builder.Services.AddSingleton<WorkerIdentity>();

// ---- Target-v1 durable execution plane -----------------------------------
builder.Services.AddSingleton<INodeCommandJournal>(sp =>
    new SqliteNodeCommandJournal(DurableDatabasePath(sp)));
builder.Services.AddSingleton<IEventSink>(sp =>
    new SqliteEventSink(DurableDatabasePath(sp)));
builder.Services.AddSingleton(sp => new LocalEventStore(sink: sp.GetRequiredService<IEventSink>()));
builder.Services.AddSingleton<IResultOutboxLog>(sp =>
    new SqliteResultOutboxLog(DurableDatabasePath(sp)));
builder.Services.AddSingleton<IResultTransport>(sp =>
    new DurableRabbitResultTransport(sp.GetRequiredService<IOptions<RabbitMqOptions>>().Value.Uri));
builder.Services.AddSingleton(sp => new LocalResultOutbox(
    sp.GetRequiredService<IResultTransport>(), () => DateTimeOffset.UtcNow,
    log: sp.GetRequiredService<IResultOutboxLog>()));
builder.Services.AddSingleton<AssignmentTracker>();
builder.Services.AddSingleton(sp => CompiledPolicy.Compile(
    sp.GetRequiredService<IOptions<DurableExecutionOptions>>().Value.PolicyPreset,
    new Dictionary<string, AgentBoard.Contracts.PolicyDecision>()));
builder.Services.AddSingleton<IApprovalGrantStore>(sp =>
    new SqliteApprovalGrantStore(DurableDatabasePath(sp)));
builder.Services.AddSingleton(sp =>
    new LocalApprovalLedger(sp.GetRequiredService<IApprovalGrantStore>()));
builder.Services.AddSingleton<DurableAssignmentRunner>(sp =>
{
    return new DurableAssignmentRunner(
        sp.GetRequiredService<WorkerIdentity>().WorkerId,
        sp.GetRequiredService<INodeCommandJournal>(),
        sp.GetRequiredService<AssignmentTracker>(),
        sp.GetRequiredService<LocalEventStore>(),
        sp.GetRequiredService<LocalResultOutbox>(),
        sp.GetRequiredService<IAgentAdapterRegistry>(),
        sp.GetRequiredService<CompiledPolicy>(),
        sp.GetRequiredService<ILocalWorkspaceResolver>(),
        approvalAuthority: sp.GetRequiredService<LocalApprovalLedger>());
});

// Readiness probe runs once at startup, after the DI graph is built.
// Each registered agent's CLI is resolved and `--version` is invoked under
// the worker's own identity (#5 in the 2026-08-28 review).
builder.Services.AddSingleton<AgentBoard.Node.Agents.ReadinessProbe>();

// ---- Hosted services -------------------------------------------------------
if (!localMode && !configurationOnly)
{
    builder.Services.AddHostedService<ExecutionDispatcher>();
    builder.Services.AddHostedService<RabbitMqConsumerService>();
    builder.Services.AddHostedService<WorkflowMqConsumerService>();
    builder.Services.AddHostedService<WorkerHeartbeatService>();
    builder.Services.AddHostedService<AgentBoardWebSocketService>();
    builder.Services.AddHostedService<WorkerStartupService>();
    builder.Services.AddHostedService<DurableCommandConsumerService>();
}

// ---- HTTP ----------------------------------------------------------------
builder.Services.AddHttpClient();

var app = builder.Build();

// One-time startup: orphan recovery. Must run after ExecutionStore is built.
using (var scope = app.Services.CreateScope())
{
    var store = scope.ServiceProvider.GetRequiredService<ExecutionStore>();
    var inbox = scope.ServiceProvider.GetRequiredService<InboxStore>();
    var workerOpts = scope.ServiceProvider.GetRequiredService<IOptions<NodeOptions>>().Value;
    var registry = scope.ServiceProvider.GetRequiredService<IAgentAdapterRegistry>();
    var log = scope.ServiceProvider.GetRequiredService<ILogger<Program>>();
    var readiness = scope.ServiceProvider.GetRequiredService<AgentBoard.Node.Agents.ReadinessProbe>();
    await store.MarkOrphansAsync(workerOpts.OrphanThresholdMinutes, CancellationToken.None);
    await inbox.ResetStuckDispatchingAsync(CancellationToken.None);

    // Pending inbox rows are NOT preloaded into the channel here on purpose.
    // The previous version did a `foreach (flight) await channel.WriteAsync`
    // BEFORE `app.Run()` started the ExecutionDispatcher HostedService, so
    // the bounded channel (capacity = 100) would deadlock when the
    // backlog exceeded 100 — the writer blocks on WriteAsync forever because
    // the consumer is not yet running. ExecutionDispatcher now drains
    // `pending` itself at startup via ListPendingAsync (see ExecutionDispatcher
    // constructor / startup), and the channel is the dispatcher→coordinator
    // hand-off only, not the durable-buffer-to-memory path (#2 in the
    // 2026-08-28 review).

    if (!localMode && !configurationOnly) await readiness.RunAllAsync(CancellationToken.None);
    log.LogInformation("AgentBoard Worker started; registered agents: [{List}]", string.Join(", ", registry.RegisteredAgents));
}

app.Use(async (context, next) =>
{
    if (context.Request.Path.StartsWithSegments("/health") || context.Request.Path == "/"
        || context.Request.Path.StartsWithSegments("/api/local"))
    {
        await next();
        return;
    }
    var portal = context.RequestServices.GetRequiredService<IOptions<PortalOptions>>().Value;
    if (string.IsNullOrWhiteSpace(portal.ApiKey) ||
        !context.Request.Headers.TryGetValue("X-AgentBoard-Worker-Key", out var key) ||
        !string.Equals(key, portal.ApiKey, StringComparison.Ordinal))
    {
        context.Response.StatusCode = StatusCodes.Status401Unauthorized;
        await context.Response.WriteAsJsonAsync(new { detail = "valid X-AgentBoard-Worker-Key required" });
        return;
    }
    await next();
});

app.MapGet("/health", (WorkerState state, IAgentAdapterRegistry registry, IOptions<NodeOptions> worker, IOptions<WorkerOwnedOptions> local) =>
    Results.Ok(state.Snapshot(local.Value.Enabled ? local.Value.Agents.Select(a => a.Id).ToArray() : registry.RegisteredAgents,
        local.Value.Enabled ? 1 : worker.Value.MaxConcurrentExecutions, state.ActiveCount, 0)));
app.MapGet("/", () => Results.Content(localMode || configurationOnly ? ConfigurationPortal.Html : PortalPage.Html, "text/html; charset=utf-8"));
ConfigurationPortal.Map(app, localConfiguration, configurationOnly);
app.MapGet("/api/worker", (WorkerState state, IAgentAdapterRegistry registry, IOptions<NodeOptions> worker, IOptions<WorkerOwnedOptions> local) =>
    Results.Ok(state.Snapshot(local.Value.Enabled ? local.Value.Agents.Select(a => a.Id).ToArray() : registry.RegisteredAgents,
        local.Value.Enabled ? 1 : worker.Value.MaxConcurrentExecutions, state.ActiveCount, 0)));
app.MapGet("/api/executions", async (ExecutionStore store, int? limit, string? agent) =>
    Results.Ok(await store.ListAsync(Math.Clamp(limit ?? 100, 1, 500), agent)));
app.MapGet("/api/executions/{id:long}", async (ExecutionStore store, long id) =>
    await store.GetAsync(id) is { } item ? Results.Ok(item) : Results.NotFound());
app.MapGet("/api/executions/{id:long}/logs", async (ExecutionStore store, long id, int? tailBytes, string? stream) =>
    Results.Ok(await store.GetLogsAsync(id, Math.Clamp(tailBytes ?? 102400, 0, 10 * 1024 * 1024), stream)));
app.MapPost("/api/control/pause", (WorkerState state) => { state.Paused = true; return Results.Ok(state.Snapshot(Array.Empty<string>(), 0, 0, 0)); });
app.MapPost("/api/control/resume", (WorkerState state) => { state.Paused = false; return Results.Ok(state.Snapshot(Array.Empty<string>(), 0, 0, 0)); });
app.MapPost("/api/executions/{id:long}/retry", async (ExecutionStore store, long id) =>
    await store.QueueRetryAsync(id) ? Results.Accepted() : Results.NotFound());
app.MapGet("/api/durable", (
    CompiledPolicy policy,
    AssignmentTracker tracker,
    INodeCommandJournal journal,
    LocalResultOutbox outbox) => Results.Ok(new
{
    policy_revision_id = policy.RevisionId,
    live_assignments = tracker.Current,
    pending_commands = journal.Pending().Select(command => command.MessageId),
    result_outbox = outbox.Records,
}));
app.MapGet("/api/durable/attempts/{attemptId}/events", (string attemptId, LocalEventStore events) =>
    Results.Ok(events.ForAttempt(attemptId)));
app.MapPost("/api/durable/approvals", (AgentBoard.Contracts.ApprovalGrant grant, LocalApprovalLedger ledger) =>
    Results.Ok(ledger.Record(grant)));

app.Run();

static string DurableDatabasePath(IServiceProvider services)
{
    var durable = services.GetRequiredService<IOptions<DurableExecutionOptions>>().Value;
    var node = services.GetRequiredService<IOptions<NodeOptions>>().Value;
    var configured = string.IsNullOrWhiteSpace(durable.DatabasePath)
        ? node.HistoryDatabasePath
        : durable.DatabasePath;
    var fullPath = Path.GetFullPath(configured);
    Directory.CreateDirectory(Path.GetDirectoryName(fullPath)!);
    return fullPath;
}
