// SPDX-License-Identifier: MIT
//
// AgentBoard.Api — Dual-stack BFF entry point.
//
// Stage 0 + Stage 7 deliverable: composition root that wires up the
// 5-layer stack (Controller → BaseController → Provider → Service →
// Repository) plus observability (Serilog + OpenTelemetry) and the
// request-id / trace-context middlewares.

using AgentBoard.Api.Api.Common;
using AgentBoard.Api.Api.Conventions;
using AgentBoard.Api.Auth;
using AgentBoard.Api.Middleware;
using AgentBoard.Api.Observability;
using AgentBoard.Application;
using AgentBoard.Application.Abstractions;
using AgentBoard.Infrastructure;
using AgentBoard.Infrastructure.Persistence;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.ApplicationModels;

var builder = WebApplication.CreateBuilder(args);

// Bind Kestrel to the AGENTBOARD_DOTNET_PORT env var when present,
// otherwise fall back to 18099 (FastAPI api 占用宿主 18000).
var dotnetPort = Environment.GetEnvironmentVariable("AGENTBOARD_DOTNET_PORT") ?? "18099";
if (!string.IsNullOrWhiteSpace(dotnetPort) && int.TryParse(dotnetPort, out var port))
{
    builder.WebHost.UseUrls($"http://0.0.0.0:{port}");
}

// --- Observability (must be wired before any ILogging/Activity consumer) -

builder.ConfigureSerilog();
builder.ConfigureOpenTelemetry();

// --- Services ---------------------------------------------------------

builder.Services.AddHttpContextAccessor();

// Outbound trace propagation: the FastAPI internal client (Stage 1) carries
// the same traceparent + X-Request-Id so the .NET -> FastAPI call continues
// one distributed trace. The handler is a no-op until the client is used.
builder.Services.AddTransient<TracePropagationDelegatingHandler>();
var fastApiInternalUrl = Environment.GetEnvironmentVariable("AGENTBOARD_FASTAPI__INTERNALURL")
    ?? "http://api:8000";
if (Uri.TryCreate(fastApiInternalUrl, UriKind.Absolute, out var fastApiUri))
{
    builder.Services
        .AddHttpClient("AgentBoardFastApi", c => c.BaseAddress = fastApiUri)
        .AddHttpMessageHandler<TracePropagationDelegatingHandler>();
}

builder.Services.AddControllers(options =>
{
    options.Conventions.Add(new ApiRouteConvention());
    options.Filters.Add<DomainExceptionFilter>();
});
builder.Services.AddOpenApi();

// Application layer (Services + Provider interfaces) — registrations
// live in AgentBoard.Application/DependencyInjection.cs.
builder.Services.AddApplication();

// Infrastructure layer (EF Core, interceptors, repositories, auth) —
// the AddInfrastructure extension chooses the right DbContext provider
// (memory / sqlite / mysql) based on configuration.
builder.Services.AddInfrastructure(builder.Configuration);

// HTTP-scoped CurrentUser — resolves the caller from X-User-* headers
// populated by the auth middleware (added in S0-7).
builder.Services.AddScoped<ICurrentUser, CurrentUserService>();

var app = builder.Build();

// --- Pipeline (order matters!) ---------------------------------------

// 1. Per-request id (logs use it; downstream middlewares read it).
app.UseMiddleware<RequestIdMiddleware>();

// 2. W3C trace context echo (Stage 1: outbound FastAPI calls).
app.UseMiddleware<TraceContextMiddleware>();

if (app.Environment.IsDevelopment())
{
    app.MapOpenApi();
}

app.MapControllers();

// Dev + Testing: ensure the SQLite / InMemory schema exists so smoke
// tests can hit the API without running `dotnet ef database update`.
// Production uses the shared MariaDB applied by the Python Alembic
// operator — never call EnsureCreated there. The WebApplicationFactory
// injects `Testing` so its per-instance temp SQLite gets a fresh schema
// before the first /api/health call hits CanConnectAsync.
if (app.Environment.IsDevelopment() || app.Environment.EnvironmentName == "Testing")
{
    using var scope = app.Services.CreateScope();
    var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
    db.Database.EnsureCreated();
}

// Temporary root endpoint so curl smoke tests succeed before S0-5
// lands the real /api/health controller. Returns 200 OK to confirm
// the host is up and bound to the expected port.
app.MapGet("/", () => Results.Ok(new
{
    service = "AgentBoard.Api",
    version = "0.7.0",
    stage = "S0-7",
    env = app.Environment.EnvironmentName,
    utcNow = DateTime.UtcNow,
}));

app.Run();

/// <summary>
/// Marker class used by WebApplicationFactory&lt;Program&gt; in integration tests.
/// </summary>
public partial class Program;
