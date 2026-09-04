// SPDX-License-Identifier: MIT
//
// AgentBoard.Api — Dual-stack BFF entry point.
//
// Stage 1 hardening deliverable: composition root that wires up the
// 5-layer stack (Controller → BaseController → Provider → Service →
// Repository) plus observability (Serilog + OpenTelemetry) and the
// request-id / trace-context middlewares.

using AgentBoard.Api.Api.Common;
using AgentBoard.Api.Api.Conventions;
using AgentBoard.Api.Auth;
using AgentBoard.Api.Durable;
using AgentBoard.Api.Middleware;
using AgentBoard.Api.Observability;
using AgentBoard.Api.Security;
using AgentBoard.Application;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Identity;
using AgentBoard.Infrastructure;
using AgentBoard.Infrastructure.Persistence;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.ApplicationModels;
using System.Text.Json;

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
builder.Services.AddAuthorization();

// Outbound trace propagation: the FastAPI internal client (Stage 1) carries
// the same traceparent + X-Request-Id so the .NET -> FastAPI call continues
// one distributed trace. The handler is a no-op until the client is used.
builder.Services.AddTransient<TracePropagationDelegatingHandler>();
// Read the FastAPI base URL from configuration so the env var
// `AgentBoard__FastApi__InternalUrl` (PascalCase, `__` delimiter) actually
// binds. The previous version read `AGENTBOARD_FASTAPI__INTERNALURL`
// (uppercase, no case folding) directly via Environment, which silently
// ignored the compose env var and always fell back to `http://api:8000`
// — fine for the default case but unable to honor operator overrides. Fix
// for the 2026-08-28 review P2.
var fastApiInternalUrl = builder.Configuration["AgentBoard:FastApi:InternalUrl"]
	?? "http://api:8000";
if (Uri.TryCreate(fastApiInternalUrl, UriKind.Absolute, out var fastApiUri))
{
	builder.Services
		.AddHttpClient("AgentBoardFastApi", c => c.BaseAddress = fastApiUri)
		.AddHttpMessageHandler<TracePropagationDelegatingHandler>();
}
builder.Services.AddScoped<AgentBoard.Api.Clients.FastApiTaskClient>();

builder.Services.AddControllers(options =>
{
	options.Conventions.Add(new ApiRouteConvention());
	options.Filters.Add<DomainExceptionFilter>();
})
// FastAPI serializes every response in snake_case (pydantic default). The .NET
// BFF must emit the identical wire format so the front-end contract is frozen
// across the dual-stack transition — no per-handler translation needed.
	.AddJsonOptions(o => o.JsonSerializerOptions.PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower);
builder.Services.AddSignalR()
	.AddJsonProtocol(options =>
		options.PayloadSerializerOptions.PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower);
builder.Services.AddOpenApi();

// Application layer (Services + Provider interfaces) — registrations
// live in AgentBoard.Application/DependencyInjection.cs.
builder.Services.AddApplication();

// Infrastructure layer (EF Core, interceptors, repositories, auth) —
// the AddInfrastructure extension chooses the right DbContext provider
// (memory / sqlite / mysql) based on configuration.
builder.Services.AddInfrastructure(builder.Configuration);
builder.Services.Configure<DurableWorkflowOptions>(builder.Configuration.GetSection("DurableWorkflow"));
builder.Services.AddScoped<DurableWorkflowGateFilter>();
builder.Services.AddSingleton<DurableServerRuntime>();
builder.Services.AddSingleton<FastApiTaskProjectionClient>();
builder.Services.AddHostedService<DurableServerOutboxService>();
builder.Services.AddHostedService<DurableServerResultConsumerService>();
builder.Services.AddHostedService<DurableTaskProjectionService>();

// Auth: PBKDF2 password hashing + stateless HMAC bearer token. Both mirror
// the FastAPI formats so the .NET BFF authenticates against the same users
// table. The JWT secret falls back to a dev default when the production
// placeholder is still in place (local dev only — never in production).
var jwtSecret = RuntimeSecurityConfiguration.ResolveJwtSecret(
	builder.Configuration,
	builder.Environment.IsDevelopment(),
	builder.Environment.IsEnvironment("Testing"));
var jwtTtl = builder.Configuration.GetValue<int>("AgentBoard:Jwt:TtlSeconds");
if (jwtTtl <= 0)
	jwtTtl = 172800;
builder.Services.AddSingleton<IPasswordHasher, Pbkdf2PasswordHasher>();
builder.Services.AddSingleton<ITokenService>(_ => new HmacTokenService(jwtSecret!, jwtTtl));

// HTTP-scoped CurrentUser — resolves the caller from X-User-* headers
// populated by the auth middleware (added in S0-7).
builder.Services.AddScoped<ICurrentUser, CurrentUserService>();

// CORS: wildcard is development-only; production must configure explicit origins.
var corsOrigins = RuntimeSecurityConfiguration.ResolveCorsOrigins(
	builder.Configuration,
	builder.Environment.IsDevelopment(),
	builder.Environment.IsEnvironment("Testing"));
builder.Services.AddCors(options =>
{
	options.AddDefaultPolicy(policy =>
	{
		policy.AllowAnyHeader().AllowAnyMethod();
		if (corsOrigins.Contains("*", StringComparer.Ordinal))
			policy.AllowAnyOrigin();
		else
			policy.WithOrigins(corsOrigins.ToArray()).AllowCredentials();
	});
});

var app = builder.Build();

// --- Pipeline (order matters!) ---------------------------------------

// 1. Per-request id (logs use it; downstream middlewares read it).
app.UseMiddleware<RequestIdMiddleware>();

// 2. W3C trace context echo (Stage 1: outbound FastAPI calls).
app.UseMiddleware<TraceContextMiddleware>();

// 2b. Bearer token resolution — populates HttpContext.User for ICurrentUser.
app.UseMiddleware<AuthMiddleware>();
app.UseAuthorization();

// 2c. API keys are authenticated above; this middleware enforces their
// read/write scope before any controller or application service executes.
app.UseMiddleware<ApiKeyPermissionMiddleware>();

if (app.Environment.IsDevelopment() || app.Environment.EnvironmentName == "Testing")
{
	app.MapOpenApi();
}

app.UseCors();
app.MapControllers();
app.MapHub<AgentBoard.Api.Realtime.ProposalHub>("/hubs/proposals");

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
	stage = "S1-hardening",
	env = app.Environment.EnvironmentName,
	utcNow = DateTime.UtcNow,
}));

app.Run();

/// <summary>
/// Marker class used by WebApplicationFactory&lt;Program&gt; in integration tests.
/// </summary>
public partial class Program;
