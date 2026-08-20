// SPDX-License-Identifier: MIT
using OpenTelemetry.Resources;
using OpenTelemetry.Trace;

namespace AgentBoard.Api.Observability;

/// <summary>
/// OpenTelemetry tracing wiring for the .NET BFF.
///
/// Resources (the attributes attached to every span):
///   - service.name = "AgentBoard.Api"
///   - service.version = from assembly informational version (or "0.0.0")
///
/// Instrumentations enabled:
///   - ASP.NET Core  — incoming HTTP request spans
///   - HttpClient    — outgoing spans (Polly + FastAPI internal calls land
///                     here in Stage 1; for now the BFF only calls out to
///                     the FastAPI health / meta from the contract tests)
///   - the custom <see cref="AgentBoardActivitySource"/> for hand-rolled
///     spans around expensive Service operations.
///
/// Exporters: console in Development (good for `dotnet run` smoke
/// tests); a real OTLP / Jaeger exporter plugs in here in S2 / S3.
/// </summary>
public static class OpenTelemetrySetup
{
    public const string ServiceName = "AgentBoard.Api";

    public static void ConfigureOpenTelemetry(this WebApplicationBuilder builder)
    {
        ArgumentNullException.ThrowIfNull(builder);

        var version = typeof(Program).Assembly.GetName().Version?.ToString() ?? "0.0.0";

        builder.Services.AddOpenTelemetry()
            .ConfigureResource(rb => rb
                .AddService(ServiceName, serviceVersion: version))
            .WithTracing(tb =>
            {
                tb.AddSource(AgentBoardActivitySource.Name)
                  .AddAspNetCoreInstrumentation(opts =>
                  {
                      // /api/health is hit by the docker healthcheck
                      // every 30s; emitting a span per probe drowns the
                      // trace log without adding signal. Skip it.
                      opts.Filter = ctx =>
                          !ctx.Request.Path.StartsWithSegments("/api/health")
                          && !ctx.Request.Path.StartsWithSegments("/openapi");
                  })
                  .AddHttpClientInstrumentation();

                // Exporters: console in dev. The OTLP exporter ships in
                // OpenTelemetry.Exporter.OpenTelemetryProtocol and is
                // added in Stage 2 once we have an actual collector to
                // ship to. Wiring it up here would pull another NuGet
                // package; we don't need it for stage 0/1 dev.
                tb.AddConsoleExporter();
            });
    }
}
