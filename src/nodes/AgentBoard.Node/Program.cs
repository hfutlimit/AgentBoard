using AgentBoard.Node;
using AgentBoard.Node.Agents;
using AgentBoard.Node.Execution;
using AgentBoard.Node.Process;
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
builder.Host.UseWindowsService(options => options.ServiceName = "AgentBoard Proposal Worker");
builder.WebHost.UseUrls(builder.Configuration["Portal:Urls"] ?? "http://127.0.0.1:58240");

// ---- Options ---------------------------------------------------------------
// P7b: ``Node`` is the canonical configuration section. The legacy ``Worker``
// section is still honoured for one release so an already-deployed
// appsettings.Local.json keeps booting after the rename, and so a rollback
// (dropping the ``Node`` section again) does not strand a live install.
var nodeSection = builder.Configuration.GetSection("Node");
if (!nodeSection.Exists())
{
    nodeSection = builder.Configuration.GetSection("Worker");
}
builder.Services.Configure<NodeOptions>(nodeSection);
builder.Services.Configure<RabbitMqOptions>(builder.Configuration.GetSection("RabbitMq"));
builder.Services.Configure<AgentsOptions>(builder.Configuration.GetSection("Agents"));
builder.Services.Configure<AgentBoardOptions>(builder.Configuration.GetSection("AgentBoard"));
builder.Services.Configure<PortalOptions>(builder.Configuration.GetSection("Portal"));
builder.Services.Configure<ProcessExecutorOptions>(builder.Configuration.GetSection("ProcessExecutor"));

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

// Readiness probe runs once at startup, after the DI graph is built.
// Each registered agent's CLI is resolved and `--version` is invoked under
// the worker's own identity (#5 in the 2026-08-28 review).
builder.Services.AddSingleton<AgentBoard.Node.Agents.ReadinessProbe>();

// ---- Hosted services -------------------------------------------------------
builder.Services.AddHostedService<ExecutionDispatcher>();
builder.Services.AddHostedService<RabbitMqConsumerService>();
builder.Services.AddHostedService<WorkflowMqConsumerService>();
builder.Services.AddHostedService<WorkerHeartbeatService>();
builder.Services.AddHostedService<AgentBoardWebSocketService>();
builder.Services.AddHostedService<WorkerStartupService>();  // PR-12

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

    await readiness.RunAllAsync(CancellationToken.None);
    log.LogInformation("AgentBoard Worker started; registered agents: [{List}]", string.Join(", ", registry.RegisteredAgents));
}

app.Use(async (context, next) =>
{
    if (context.Request.Path.StartsWithSegments("/health") || context.Request.Path == "/")
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

app.MapGet("/health", (WorkerState state, IAgentAdapterRegistry registry, IOptions<NodeOptions> worker) =>
    Results.Ok(state.Snapshot(registry.RegisteredAgents, worker.Value.MaxConcurrentExecutions, state.ActiveCount, 0)));
app.MapGet("/", () => Results.Content(PortalPage.Html, "text/html; charset=utf-8"));
app.MapGet("/api/worker", (WorkerState state, IAgentAdapterRegistry registry, IOptions<NodeOptions> worker) =>
    Results.Ok(state.Snapshot(registry.RegisteredAgents, worker.Value.MaxConcurrentExecutions, state.ActiveCount, 0)));
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

app.Run();
