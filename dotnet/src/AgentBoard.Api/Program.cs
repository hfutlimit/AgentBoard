// SPDX-License-Identifier: MIT
//
// AgentBoard.Api — Dual-stack BFF entry point.
//
// Stage 0 deliverable: minimal Program.cs that starts the host on
// the configured port and exposes the OpenAPI document. No business
// endpoints are wired up yet; /api/health and /api/meta land in S0-5.
//
// Wiring principles established here (extended in later stories):
//   1. Composition root only — no business logic, no DI factories.
//   2. Configuration bound via the AgentBoard:* section.
//   3. OpenAPI document always served (NSwag is added in S0-4).
//   4. Kestrel binds to 0.0.0.0 so docker-compose port mapping works.

var builder = WebApplication.CreateBuilder(args);

// Bind Kestrel to the AGENTBOARD_DOTNET_PORT env var when present,
// otherwise fall back to 18000 (see launchSettings.json).
var dotnetPort = Environment.GetEnvironmentVariable("AGENTBOARD_DOTNET_PORT");
if (!string.IsNullOrWhiteSpace(dotnetPort) && int.TryParse(dotnetPort, out var port))
{
    builder.WebHost.UseUrls($"http://0.0.0.0:{port}");
}

// --- Services ---------------------------------------------------------

builder.Services.AddControllers();
builder.Services.AddOpenApi();

var app = builder.Build();

// --- Pipeline ---------------------------------------------------------

if (app.Environment.IsDevelopment())
{
    app.MapOpenApi();
}

app.MapControllers();

// Temporary root endpoint so curl smoke tests succeed before S0-5
// lands the real /api/health controller. Returns 200 OK to confirm
// the host is up and bound to the expected port.
app.MapGet("/", () => Results.Ok(new
{
    service = "AgentBoard.Api",
    version = "0.1.0",
    stage = "S0-1",
    env = app.Environment.EnvironmentName,
    utcNow = DateTime.UtcNow,
}));

app.Run();

/// <summary>
/// Marker class used by WebApplicationFactory&lt;Program&gt; in integration tests.
/// </summary>
public partial class Program;
