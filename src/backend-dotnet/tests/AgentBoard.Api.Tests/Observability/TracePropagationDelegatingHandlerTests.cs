// SPDX-License-Identifier: MIT
using System.Diagnostics;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using AgentBoard.Api.Middleware;
using AgentBoard.Api.Observability;
using FluentAssertions;
using Microsoft.AspNetCore.Http;
using Xunit;

namespace AgentBoard.Api.Tests.Observability;

/// <summary>Verifies the BFF carries its correlation context onto outbound
/// calls to FastAPI (the "real cross-stack trace" for #313 / S0-7).</summary>
public class TracePropagationDelegatingHandlerTests
{
    private sealed class CapturingHandler : DelegatingHandler
    {
        public HttpRequestMessage? Captured { get; private set; }

        protected override Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request, CancellationToken cancellationToken)
        {
            Captured = request;
            return Task.FromResult(new HttpResponseMessage(System.Net.HttpStatusCode.OK));
        }
    }

    [Fact]
    public async Task Injects_TraceParent_And_RequestId_Into_Outbound()
    {
        var ctx = new DefaultHttpContext();
        ctx.Items[RequestIdMiddleware.ContextKey] = "req-xyz";
        var accessor = new HttpContextAccessor { HttpContext = ctx };

        var activity = new Activity("outbound-test").Start();
        try
        {
            var capturing = new CapturingHandler();
            var handler = new TracePropagationDelegatingHandler(accessor)
            {
                InnerHandler = capturing,
            };
            using var client = new HttpClient(handler);

            var request = new HttpRequestMessage(HttpMethod.Get, "http://fastapi/health");

            await client.SendAsync(request, CancellationToken.None);

            var captured = capturing.Captured!;
            captured.Headers.GetValues(TracePropagationDelegatingHandler.RequestIdHeader)
                .Should().ContainSingle().Which.Should().Be("req-xyz");
            captured.Headers.GetValues("traceparent")
                .Should().ContainSingle().Which.Should().Be(activity.Id);
        }
        finally
        {
            activity.Stop();
        }
    }

    [Fact]
    public async Task Does_Not_Overwrite_Already_Present_Headers()
    {
        var accessor = new HttpContextAccessor(); // no active request context
        var activity = new Activity("x").Start();
        try
        {
            var capturing = new CapturingHandler();
            var handler = new TracePropagationDelegatingHandler(accessor)
            {
                InnerHandler = capturing,
            };
            using var client = new HttpClient(handler);
            var request = new HttpRequestMessage(HttpMethod.Get, "http://fastapi/health");
            request.Headers.TryAddWithoutValidation("traceparent", "preset-tp");
            request.Headers.TryAddWithoutValidation(
                TracePropagationDelegatingHandler.RequestIdHeader, "preset-rid");

            await client.SendAsync(request, CancellationToken.None);

            var captured = capturing.Captured!;
            captured.Headers.GetValues("traceparent")
                .Should().ContainSingle().Which.Should().Be("preset-tp");
            captured.Headers.GetValues(TracePropagationDelegatingHandler.RequestIdHeader)
                .Should().ContainSingle().Which.Should().Be("preset-rid");
        }
        finally
        {
            activity.Stop();
        }
    }
}
