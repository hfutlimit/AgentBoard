using System.Net;
using System.Text;
using AgentBoard.Application.Abstractions;
using AgentBoard.Infrastructure.Scheduling;
using FluentAssertions;
using Microsoft.Extensions.Configuration;
using NSubstitute;

namespace AgentBoard.Infrastructure.Tests.Scheduling;

public sealed class WorkflowWorkContextResolverTests
{
    [Theory]
    [InlineData("{\"blockers\":[],\"blocked_by\":[{\"task_id\":2,\"type\":\"blocks\",\"task\":{\"id\":2,\"status\":\"todo\"}}]}")]
    [InlineData("{\"blockers\":[{\"task_id\":2,\"type\":\"blocks\",\"task\":{\"id\":2,\"status\":\"done\"}}],\"blocked_by\":[]}")]
    [InlineData("{\"blockers\":[{\"task_id\":2,\"type\":\"relates_to\",\"task\":null},{\"task_id\":3,\"type\":\"blocked_by\",\"task\":null}],\"blocked_by\":[]}")]
    public async Task Reverse_dependents_completed_and_informational_edges_do_not_block(string json)
    {
        using var handler = new DependencyHandler(json);
        var resolution = await Resolve(handler);
        resolution.Status.Should().Be(WorkflowWorkResolutionStatus.Found);
        resolution.Context.Should().NotBeNull();
        handler.Paths.Should().Equal("/api/tasks/1", "/api/tasks/1/dependencies");
        handler.Auth.Should().OnlyContain(value => value == "Bearer test-service-key");
    }

    [Theory]
    [InlineData("{\"blockers\":[{\"task_id\":2,\"type\":\"blocks\",\"task\":{\"id\":2,\"status\":\"todo\"}}],\"blocked_by\":[]}")]
    [InlineData("{\"blockers\":[{\"task_id\":2,\"type\":\"blocks\",\"task\":null}],\"blocked_by\":[]}")]
    public async Task Unfinished_and_missing_prerequisites_block(string json)
    {
        using var handler = new DependencyHandler(json);
        var result = await Resolve(handler);
        result.Status.Should().Be(WorkflowWorkResolutionStatus.DependenciesNotReady);
        result.Context.Should().BeNull();
        result.BlockingTaskIds.Should().Equal(2);
    }

    [Theory]
    [InlineData(401)]
    [InlineData(403)]
    [InlineData(404)]
    [InlineData(500)]
    [InlineData(503)]
    public async Task Non_success_dependency_reads_fail_closed(int status)
    {
        using var handler = new DependencyHandler("{\"blockers\":[]}", (HttpStatusCode)status);
        var result = await Resolve(handler);
        result.Status.Should().Be(WorkflowWorkResolutionStatus.DependenciesUnavailable);
        result.Context.Should().BeNull();
    }

    [Theory]
    [InlineData("not json")]
    [InlineData("null")]
    [InlineData("[]")]
    [InlineData("{}")]
    [InlineData("{\"blocked_by\":[]}")]
    [InlineData("{\"blockers\":null}")]
    [InlineData("{\"blockers\":{}}")]
    [InlineData("{\"blockers\":[null]}")]
    [InlineData("{\"blockers\":[{}]}")]
    [InlineData("{\"blockers\":[{\"type\":\"unknown\"}]}")]
    [InlineData("{\"blockers\":[{\"type\":\"blocks\",\"task_id\":0,\"task\":null}]}")]
    [InlineData("{\"blockers\":[{\"type\":\"blocks\",\"task_id\":2}]}")]
    [InlineData("{\"blockers\":[{\"type\":\"blocks\",\"task_id\":{},\"task\":null}]}")]
    [InlineData("{\"blockers\":[{\"type\":\"blocks\",\"task_id\":2,\"task\":[]}]}")]
    [InlineData("{\"blockers\":[{\"type\":\"blocks\",\"task_id\":2,\"task\":{\"id\":3,\"status\":\"done\"}}]}")]
    [InlineData("{\"blockers\":[{\"type\":\"blocks\",\"task_id\":2,\"task\":{\"id\":2}}]}")]
    public async Task Unverifiable_dependency_payloads_fail_closed(string json)
    {
        using var handler = new DependencyHandler(json);
        var result = await Resolve(handler);
        result.Status.Should().Be(WorkflowWorkResolutionStatus.DependenciesUnavailable);
        result.Context.Should().BeNull();
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public async Task Connection_failure_and_timeout_fail_closed(bool timeout)
    {
        using var handler = new DependencyHandler("{}")
        {
            Failure = timeout ? new TaskCanceledException("timeout") : new HttpRequestException("offline"),
        };
        (await Resolve(handler)).Status.Should().Be(WorkflowWorkResolutionStatus.DependenciesUnavailable);
    }

    [Fact]
    public async Task Caller_cancellation_is_not_disguised_as_upstream_failure()
    {
        using var cancellation = new CancellationTokenSource();
        using var handler = new DependencyHandler("{}") { OnDependencies = cancellation.Cancel };
        var call = () => Resolve(handler, cancellation.Token);
        await call.Should().ThrowAsync<OperationCanceledException>();
    }

    private static async Task<WorkflowWorkResolution> Resolve(DependencyHandler handler, CancellationToken token = default)
    {
        using var client = new HttpClient(handler, disposeHandler: false) { BaseAddress = new Uri("http://business/") };
        var factory = Substitute.For<IHttpClientFactory>();
        factory.CreateClient("AgentBoardFastApi").Returns(client);
        var config = new ConfigurationBuilder().AddInMemoryCollection(new Dictionary<string, string?>
        {
            ["AgentBoard:FastApi:InternalToken"] = "test-service-key",
        }).Build();
        return await new WorkflowWorkContextResolver(factory, config)
            .ResolveTaskAsync(1, "workspace", "base", "context", token);
    }

    private sealed class DependencyHandler(string json, HttpStatusCode status = HttpStatusCode.OK) : HttpMessageHandler
    {
        public Exception? Failure { get; init; }
        public Action? OnDependencies { get; init; }
        public List<string> Paths { get; } = [];
        public List<string?> Auth { get; } = [];

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            Paths.Add(request.RequestUri!.AbsolutePath);
            Auth.Add(request.Headers.Authorization?.ToString());
            var dependency = request.RequestUri.AbsolutePath.EndsWith("/dependencies");
            if (dependency)
            {
                OnDependencies?.Invoke();
                cancellationToken.ThrowIfCancellationRequested();
                if (Failure is not null) throw Failure;
            }
            return Task.FromResult(new HttpResponseMessage(dependency ? status : HttpStatusCode.OK)
            {
                Content = new StringContent(dependency ? json :
                    """{"id":1,"project_id":8,"owner_user_id":4,"status":"todo","type":"dev","needed_capabilities":"[]"}""",
                    Encoding.UTF8, "application/json"),
            });
        }
    }
}
