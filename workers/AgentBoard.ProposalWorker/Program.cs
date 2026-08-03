using AgentBoard.ProposalWorker;
using Microsoft.Extensions.Options;

var builder = WebApplication.CreateBuilder(args);
builder.Host.UseWindowsService(options => options.ServiceName = "AgentBoard Proposal Worker");
builder.WebHost.UseUrls(builder.Configuration["Portal:Urls"] ?? "http://127.0.0.1:58240");

builder.Services.Configure<WorkerOptions>(builder.Configuration.GetSection("Worker"));
builder.Services.Configure<RabbitMqOptions>(builder.Configuration.GetSection("RabbitMq"));
builder.Services.Configure<WorkBuddyOptions>(builder.Configuration.GetSection("WorkBuddy"));
builder.Services.Configure<AgentBoardOptions>(builder.Configuration.GetSection("AgentBoard"));
builder.Services.Configure<PortalOptions>(builder.Configuration.GetSection("Portal"));
builder.Services.AddHttpClient();
builder.Services.AddSingleton<WorkerState>();
builder.Services.AddSingleton<ExecutionStore>();
builder.Services.AddSingleton<WorkBuddyRunner>();
builder.Services.AddSingleton<ProposalExecutionService>();
builder.Services.AddHostedService<RabbitMqConsumerService>();
builder.Services.AddHostedService<WorkerHeartbeatService>();
builder.Services.AddHostedService<AgentBoardWebSocketService>();
builder.Services.AddHostedService<PortalRetryService>();

var app = builder.Build();
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

app.MapGet("/health", (WorkerState state) => Results.Ok(state.Snapshot()));
app.MapGet("/", () => Results.Content(PortalPage.Html, "text/html; charset=utf-8"));
app.MapGet("/api/worker", (WorkerState state) => Results.Ok(state.Snapshot()));
app.MapGet("/api/executions", async (ExecutionStore store, int? limit) =>
    Results.Ok(await store.ListAsync(Math.Clamp(limit ?? 100, 1, 500))));
app.MapGet("/api/executions/{id:long}", async (ExecutionStore store, long id) =>
    await store.GetAsync(id) is { } item ? Results.Ok(item) : Results.NotFound());
app.MapPost("/api/control/pause", (WorkerState state) => { state.Paused = true; return Results.Ok(state.Snapshot()); });
app.MapPost("/api/control/resume", (WorkerState state) => { state.Paused = false; return Results.Ok(state.Snapshot()); });
app.MapPost("/api/executions/{id:long}/retry", async (ExecutionStore store, long id) =>
    await store.QueueRetryAsync(id) ? Results.Accepted() : Results.NotFound());

app.Run();
