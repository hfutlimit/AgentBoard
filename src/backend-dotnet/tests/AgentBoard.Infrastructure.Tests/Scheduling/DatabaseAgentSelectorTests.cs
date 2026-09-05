// SPDX-License-Identifier: MIT
using System.Net;
using System.Text;
using AgentBoard.Contracts;
using AgentBoard.Domain.Workflow.Durable;
using AgentBoard.Infrastructure.Scheduling;
using FluentAssertions;
using Microsoft.Extensions.Configuration;
using NSubstitute;
using Xunit;

namespace AgentBoard.Infrastructure.Tests.Scheduling;

/// <summary>
/// The selector is now a thin HTTP client around FastAPI's
/// <c>POST /api/durable/agent-select</c>. These tests assert request shaping
/// (snake_case payload + bearer credential) and fail-closed response handling;
/// the authoritative ownership/heartbeat/capability policy is exercised by the
/// FastAPI feature tests.
/// </summary>
public sealed class DatabaseAgentSelectorTests
{
    private const string Token = "abk_test_internal_service_key";

    [Fact]
    public void Select_posts_authorized_request_and_parses_selection()
    {
        var handler = new StubHandler(
            HttpStatusCode.OK,
            """
            {"selection":{
                "worker_id":"worker-review",
                "agent_id":"agent.review",
                "capabilities":["development","review"],
                "provider_id":"scenario"
            }}
            """);

        var selector = BuildSelector(handler, Token);
        var request = new AgentSelectionRequest(
            "run-1",
            "stage-review",
            StageType.Review,
            3,
            7,
            new[] { new AgentCapabilityRequirement("review", 2) },
            new HashSet<string>(new[] { "agent.self" }, StringComparer.Ordinal));

        var selected = selector.Select(request);

        selected.Should().NotBeNull();
        selected!.WorkerId.Should().Be("worker-review");
        selected.AgentId.Should().Be("agent.review");
        selected.Capabilities.Should().BeEquivalentTo(new[] { "development", "review" });
        selected.ProviderId.Should().Be("scenario");

        handler.Called.Should().BeTrue();
        handler.Path.Should().Be("api/durable/agent-select");
        handler.Authorization.Should().Be($"Bearer {Token}");
        handler.Body.Should().Contain("\"project_id\":3");
        handler.Body.Should().Contain("\"owner_user_id\":7");
        handler.Body.Should().Contain("\"minimum_level\":2");
        handler.Body.Should().Contain("agent.self");
    }

    [Fact]
    public void Select_returns_null_when_selection_is_empty()
    {
        var handler = new StubHandler(HttpStatusCode.OK, """{"selection":null,"reason":"no-eligible-agent"}""");
        BuildSelector(handler, Token).Select(SampleRequest()).Should().BeNull();
    }

    [Fact]
    public void Select_returns_null_on_non_success_status()
    {
        var handler = new StubHandler(HttpStatusCode.InternalServerError, "{}");
        BuildSelector(handler, Token).Select(SampleRequest()).Should().BeNull();
    }

    [Fact]
    public void Select_fails_closed_without_a_configured_credential()
    {
        var handler = new StubHandler(HttpStatusCode.OK, """{"selection":{"worker_id":"w","agent_id":"a","capabilities":[],"provider_id":"codex"}}""");
        // Placeholder token → must not even reach the network.
        BuildSelector(handler, "REPLACE_WITH_INTERNAL_SERVICE_TOKEN")
            .Select(SampleRequest()).Should().BeNull();
        handler.Called.Should().BeFalse("a placeholder credential must not authenticate upstream");

        BuildSelector(handler, "").Select(SampleRequest()).Should().BeNull();
        handler.Called.Should().BeFalse();
    }

    [Fact]
    public void Capability_contracts_parse_levels_and_reject_malformed_shapes()
    {
        var requirements = AgentCapabilityJson.ParseRequirements(
            "[\"development\",{\"name\":\"review\",\"minimum_level\":4}]");

        requirements.Should().ContainEquivalentOf(new AgentCapabilityRequirement("development", 1));
        requirements.Should().ContainEquivalentOf(new AgentCapabilityRequirement("review", 4));
        FluentActions.Invoking(() => AgentCapabilityJson.ParseRequirements("{}"))
            .Should().Throw<AgentBoard.Domain.Common.InvalidValueException>();
    }

    private static AgentSelectionRequest SampleRequest() => new(
        "run-1",
        "stage-dev",
        StageType.Development,
        3,
        7,
        Array.Empty<AgentCapabilityRequirement>(),
        new HashSet<string>(StringComparer.Ordinal));

    private static DatabaseAgentSelector BuildSelector(HttpMessageHandler handler, string token)
    {
        var client = new HttpClient(handler) { BaseAddress = new Uri("http://127.0.0.1:8000/") };
        var factory = Substitute.For<IHttpClientFactory>();
        factory.CreateClient(Arg.Any<string>()).Returns(client);
        var configuration = new ConfigurationBuilder()
            .AddInMemoryCollection(new Dictionary<string, string?>
            {
                ["AgentBoard:FastApi:InternalToken"] = token,
            })
            .Build();
        return new DatabaseAgentSelector(factory, configuration);
    }

    private sealed class StubHandler : HttpMessageHandler
    {
        private readonly HttpStatusCode _status;
        private readonly string _body;

        public StubHandler(HttpStatusCode status, string body)
        {
            _status = status;
            _body = body;
        }

        public bool Called { get; private set; }
        public string Path { get; private set; } = string.Empty;
        public string? Authorization { get; private set; }
        public string Body { get; private set; } = string.Empty;

        protected override async Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request, CancellationToken cancellationToken)
        {
            Called = true;
            Path = request.RequestUri?.AbsolutePath.TrimStart('/') ?? string.Empty;
            Authorization = request.Headers.Authorization is { } auth
                ? $"{auth.Scheme} {auth.Parameter}"
                : null;
            Body = request.Content is null
                ? string.Empty
                : Uri.UnescapeDataString(await request.Content.ReadAsStringAsync(cancellationToken));
            return new HttpResponseMessage(_status)
            {
                Content = new StringContent(_body, Encoding.UTF8, "application/json"),
            };
        }
    }
}
