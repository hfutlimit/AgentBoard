// SPDX-License-Identifier: MIT
using System.Diagnostics;
using AgentBoard.Api.Middleware;
using Microsoft.AspNetCore.Http;

namespace AgentBoard.Api.Observability;

/// <summary>
/// Propagates the BFF's correlation context onto outbound HTTP calls
/// (Stage 1: the FastAPI internal endpoints). Two headers are carried:
///   - <c>traceparent</c> — the W3C trace id of the currently active
///     <see cref="Activity"/> (continues the same distributed trace across
///     the .NET -> FastAPI stack). Only added when not already present, so
///     it never fights the SocketsHttpHandler's own W3C injection.
///   - <c>X-Request-Id</c> — the per-request id stamped by
///     <see cref="RequestIdMiddleware"/>, so the FastAPI side can stitch
///     its logs to the same request without re-parsing traceparent.
///
/// The handler is registered on the <c>AgentBoardFastApi</c> HttpClient; it
/// is a no-op for requests that already carry these headers.
/// </summary>
public sealed class TracePropagationDelegatingHandler : DelegatingHandler
{
    public const string RequestIdHeader = "X-Request-Id";

    private readonly IHttpContextAccessor _httpContextAccessor;

    public TracePropagationDelegatingHandler(IHttpContextAccessor httpContextAccessor)
    {
        _httpContextAccessor = httpContextAccessor;
    }

    protected override Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(request);

        // W3C trace context — continue the active trace onto the outbound call.
        if (Activity.Current is { } activity && !request.Headers.Contains("traceparent"))
        {
            request.Headers.TryAddWithoutValidation("traceparent", activity.Id);
        }

        // Per-request id — pulled from the middleware's Items bag or the
        // inbound header, whichever is available.
        var ctx = _httpContextAccessor.HttpContext;
        string? requestId = null;
        if (ctx is not null)
        {
            requestId = ctx.Items[RequestIdMiddleware.ContextKey] as string
                ?? ctx.Request.Headers[RequestIdMiddleware.HeaderName].ToString();
        }

        if (!string.IsNullOrWhiteSpace(requestId) && !request.Headers.Contains(RequestIdHeader))
        {
            request.Headers.TryAddWithoutValidation(RequestIdHeader, requestId);
        }

        return base.SendAsync(request, cancellationToken);
    }
}
