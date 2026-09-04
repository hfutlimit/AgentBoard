// SPDX-License-Identifier: MIT
using System.Net;
using System.Text;
using AgentBoard.Api.Durable;
using AgentBoard.Domain.Workflow.Durable;
using FluentAssertions;
using Microsoft.Extensions.Configuration;

namespace AgentBoard.Api.Tests.Features;

public sealed class DurableTaskProjectionClientTests
{
    [Fact]
    public async Task Projection_uses_task_state_machine_and_completes_ready_story()
    {
        var handler = new QueueHandler(
            Json(HttpStatusCode.OK, "{\"id\":42,\"status\":\"in_review\",\"story_id\":7}"),
            Json(HttpStatusCode.OK, "{\"id\":42,\"status\":\"done\",\"story_id\":7}"),
            Json(HttpStatusCode.OK, "{\"items\":[{\"id\":42,\"status\":\"done\"}]}"),
            Json(HttpStatusCode.OK, "{\"id\":7,\"status\":\"done\"}"));
        var client = CreateClient(handler, "service-token");
        var projection = new TaskStatusProjection(
            "projection-1", "run-1", 42, "done", "completed", "workflow succeeded",
            TaskStatusProjectionState.Dispatching, 1, DateTimeOffset.UtcNow);

        await client.ProjectAsync(projection, CancellationToken.None);

        handler.Requests.Select(request => $"{request.Method} {request.Path}").Should().Equal(
            "GET /api/tasks/42",
            "PUT /api/tasks/42/status",
            "GET /api/stories/7/tasks?limit=200",
            "POST /api/stories/7/complete");
        handler.Requests.Should().OnlyContain(request => request.Authorization == "Bearer service-token");
        handler.Requests[1].Body.Should().Contain("\"status\":\"done\"");
        handler.Requests[1].Body.Should().Contain("\"status_reason\":\"completed\"");
    }

    [Fact]
    public async Task Replayed_projection_is_idempotent_when_task_already_has_target_status()
    {
        var handler = new QueueHandler(
            Json(HttpStatusCode.OK, "{\"id\":42,\"status\":\"in_progress\",\"story_id\":null}"));
        var client = CreateClient(handler, "");
        var projection = new TaskStatusProjection(
            "projection-1", "run-1", 42, "in_progress", null, "workflow started",
            TaskStatusProjectionState.Dispatching, 2, DateTimeOffset.UtcNow);

        await client.ProjectAsync(projection, CancellationToken.None);

        handler.Requests.Should().ContainSingle();
        handler.Requests[0].Method.Should().Be("GET");
    }

    private static FastApiTaskProjectionClient CreateClient(QueueHandler handler, string token)
    {
        var http = new HttpClient(handler) { BaseAddress = new Uri("http://fastapi.test/") };
        var configuration = new ConfigurationBuilder().AddInMemoryCollection(new Dictionary<string, string?>
        {
            ["AgentBoard:FastApi:InternalToken"] = token,
        }).Build();
        return new FastApiTaskProjectionClient(new StaticHttpClientFactory(http), configuration);
    }

    private static HttpResponseMessage Json(HttpStatusCode status, string body) => new(status)
    {
        Content = new StringContent(body, Encoding.UTF8, "application/json"),
    };

    private sealed class StaticHttpClientFactory(HttpClient client) : IHttpClientFactory
    {
        public HttpClient CreateClient(string name) => client;
    }

    private sealed class QueueHandler(params HttpResponseMessage[] responses) : HttpMessageHandler
    {
        private readonly Queue<HttpResponseMessage> _responses = new(responses);
        public List<RecordedRequest> Requests { get; } = new();

        protected override async Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request,
            CancellationToken cancellationToken)
        {
            Requests.Add(new RecordedRequest(
                request.Method.Method,
                request.RequestUri!.PathAndQuery,
                request.Headers.Authorization?.ToString(),
                request.Content is null ? "" : await request.Content.ReadAsStringAsync(cancellationToken)));
            return _responses.Dequeue();
        }
    }

    private sealed record RecordedRequest(string Method, string Path, string? Authorization, string Body);
}
