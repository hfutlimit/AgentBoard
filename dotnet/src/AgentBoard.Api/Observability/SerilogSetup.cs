// SPDX-License-Identifier: MIT
using Serilog;
using Serilog.Core;
using Serilog.Events;
using Serilog.Formatting.Compact;

namespace AgentBoard.Api.Observability;

/// <summary>
/// Serilog wiring for the .NET BFF. Host bootstrap is the single place
/// that reads <c>Serilog:*</c> from configuration, so the rest of the
/// application can use the standard <c>ILogger&lt;T&gt;</c> abstraction.
///
/// Output:
///   - console:  CLEF (Compact Log Event Format) JSON — easy to ship to
///               any aggregator that can parse newline-delimited JSON.
///   - rolling file: <c>Logs/agentboard-dotnet-.log</c>, 14-day retention.
///
/// Trace correlation: every log event inside an active trace is enriched
/// with <c>trace_id</c> and <c>span_id</c> from the OpenTelemetry
/// <see cref="System.Diagnostics.Activity.Current"/>. The
/// <c>RequestIdMiddleware</c> additionally pushes the per-request
/// <c>request_id</c> into the LogContext so it shows up on every line
/// for that request.
/// </summary>
public static class SerilogSetup
{
    public static void ConfigureSerilog(this WebApplicationBuilder builder)
    {
        ArgumentNullException.ThrowIfNull(builder);

        builder.Host.UseSerilog((ctx, services, lc) =>
        {
            lc.ReadFrom.Configuration(ctx.Configuration)
              .ReadFrom.Services(services)
              .Enrich.FromLogContext()
              .Enrich.WithProperty("Application", "AgentBoard.Api")
              .Enrich.WithProperty("MachineName", Environment.MachineName)
              // Pull trace_id / span_id from the current Activity when present.
              .Enrich.With(new TraceContextEnricher())
              .MinimumLevel.Override("Microsoft", LogEventLevel.Warning)
              .MinimumLevel.Override("Microsoft.Hosting.Lifetime", LogEventLevel.Information)
              .MinimumLevel.Override("Microsoft.EntityFrameworkCore", LogEventLevel.Warning)
              .MinimumLevel.Override("System", LogEventLevel.Warning)
              .WriteTo.Console(new CompactJsonFormatter());

            // File sink is only useful in real environments — tests get
            // noisy and the rolling file is locked when the test host
            // tears down. The OTel console exporter already covers the
            // trace surface; logs go to console for the same reason.
            if (!ctx.HostingEnvironment.IsEnvironment("Testing"))
            {
                lc.WriteTo.File(
                    formatter: new CompactJsonFormatter(),
                    path: "Logs/agentboard-dotnet-.log",
                    rollingInterval: RollingInterval.Day,
                    retainedFileCountLimit: 14);
            }
        });
    }
}

/// <summary>Adds <c>trace_id</c> and <c>span_id</c> to the log event when
/// an <see cref="System.Diagnostics.Activity"/> is active.</summary>
internal sealed class TraceContextEnricher : ILogEventEnricher
{
    public void Enrich(LogEvent logEvent, ILogEventPropertyFactory propertyFactory)
    {
        var activity = System.Diagnostics.Activity.Current;
        if (activity is null) return;

        logEvent.AddPropertyIfAbsent(
            propertyFactory.CreateProperty("trace_id", activity.TraceId.ToString()));
        logEvent.AddPropertyIfAbsent(
            propertyFactory.CreateProperty("span_id", activity.SpanId.ToString()));
    }
}
