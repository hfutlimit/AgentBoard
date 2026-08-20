// SPDX-License-Identifier: MIT
using Serilog.Context;

namespace AgentBoard.Api.Middleware;

/// <summary>
/// Resolves or generates the <c>X-Request-Id</c> header for every
/// request, pushes it into the <see cref="HttpContext.Items"/> bag and
/// the Serilog <see cref="LogContext"/>, and echoes it back in the
/// response so callers (and the nginx access log) can correlate.
///
/// Header conventions: <c>X-Request-Id</c> for the public value,
/// <c>traceparent</c> for the W3C trace context (handled by
/// <see cref="TraceContextMiddleware"/>) — the two are correlated but
/// not identical.
/// </summary>
public sealed class RequestIdMiddleware
{
    public const string HeaderName = "X-Request-Id";
    public const string ContextKey = "AgentBoard.RequestId";

    private readonly RequestDelegate _next;

    public RequestIdMiddleware(RequestDelegate next) => _next = next;

    public async Task Invoke(HttpContext ctx)
    {
        ArgumentNullException.ThrowIfNull(ctx);

        var incoming = ctx.Request.Headers[HeaderName].ToString();
        var requestId = string.IsNullOrWhiteSpace(incoming) || incoming.Length > 128
            ? Guid.NewGuid().ToString("N")
            : incoming;

        ctx.Items[ContextKey] = requestId;
        ctx.Response.Headers[HeaderName] = requestId;

        using (LogContext.PushProperty("request_id", requestId))
        {
            await _next(ctx);
        }
    }
}

public static class RequestIdMiddlewareExtensions
{
    public static IApplicationBuilder UseRequestId(this IApplicationBuilder app) =>
        app.UseMiddleware<RequestIdMiddleware>();
}
