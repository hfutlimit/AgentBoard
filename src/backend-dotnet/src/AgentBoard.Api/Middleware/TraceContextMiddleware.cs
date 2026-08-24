// SPDX-License-Identifier: MIT
using System.Diagnostics;

namespace AgentBoard.Api.Middleware;

/// <summary>
/// Honours the W3C <c>traceparent</c> header on incoming requests:
///   - If present, the ASP.NET Core hosting layer continues the trace.
///   - If absent, a fresh <see cref="Activity"/> is started and a
///     <c>traceparent</c> is added to the response so the caller can
///     correlate the next leg of the flow.
/// </summary>
/// <remarks>
/// This middleware is mostly a placeholder for Stage 1, when the BFF
/// starts calling the FastAPI internal endpoints. Today the request
/// flow is simple enough that the ASP.NET Core hosting layer + the
/// <see cref="RequestIdMiddleware"/> cover the observability needs; the
/// explicit <c>traceparent</c> echo becomes important when the BFF
/// fans out to the FastAPI AI subsystem.
/// </remarks>
public sealed class TraceContextMiddleware
{
    public const string TraceParentHeader = "traceparent";

    private readonly RequestDelegate _next;

    public TraceContextMiddleware(RequestDelegate next) => _next = next;

    public async Task Invoke(HttpContext ctx)
    {
        ArgumentNullException.ThrowIfNull(ctx);

        // ASP.NET Core 8+ already understands the W3C trace context;
        // we just stamp the response with the traceparent so external
        // callers (load balancers, the FastAPI AI subsystem) can stitch
        // traces together.
        if (Activity.Current is { } activity)
        {
            ctx.Response.Headers[TraceParentHeader] = activity.Id ?? string.Empty;
        }

        await _next(ctx);
    }
}

public static class TraceContextMiddlewareExtensions
{
    public static IApplicationBuilder UseTraceContext(this IApplicationBuilder app) =>
        app.UseMiddleware<TraceContextMiddleware>();
}
