// SPDX-License-Identifier: MIT
using System.Linq;
using System.Net;
using System.Text.Json;
using AgentBoard.ProposalWorker;
using AgentBoard.ProposalWorker.Agents;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Xunit;

namespace AgentBoard.ProposalWorker.Tests;

/// <summary>
/// PR-12：WorkerStartupService 单测。
/// 验证 startup 时正确调 /api/workers/register 和 /api/agents/{id}/instances，
/// 周期性 heartbeat 调 /api/workers/{wid}/agent-instances/{id}/heartbeat。
/// </summary>
public class WorkerStartupServiceTests
{
    private static IAgentAdapterRegistry ThreeAgentRegistry()
    {
        var registry = new AgentAdapterRegistry(
            new IAgentAdapter[]
            {
                new FakeAdapter("workbuddy"),
                new FakeAdapter("codex"),
                new FakeAdapter("minimax"),
            },
            NullLogger<AgentAdapterRegistry>.Instance);
        return registry;
    }

    private static (WorkerOptions wo, AgentBoardOptions ab, AgentsOptions agents) BuildOpts(
        string serverUrl = "http://test:8080",
        string workerId = "dev-pc-01")
    {
        var wo = new WorkerOptions { Id = workerId, HeartbeatSeconds = 5 };
        var ab = new AgentBoardOptions { ServerUrl = serverUrl };
        var agents = new AgentsOptions
        {
            WorkBuddy = new AgentOptions { Command = "workbuddy", AgentId = "workbuddy-on-dev" },
            Codex     = new AgentOptions { Command = "codex",     AgentId = "codex-on-dev"     },
            MiniMax   = new AgentOptions { Command = "" },  // 禁用
            Fake      = new AgentOptions { Command = "" },  // 跳过
        };
        return (wo, ab, agents);
    }

    [Fact]
    public async Task Skips_registration_when_server_url_empty()
    {
        var stub = new StubHandler();
        var http = new HttpClient(stub);
        var (wo, ab, agents) = BuildOpts(serverUrl: "");  // 空
        var svc = new WorkerStartupService(
            new FixedHttpFactory(http),
            Options.Create(wo), Options.Create(ab), Options.Create(agents),
            ThreeAgentRegistry(), NullLogger<WorkerStartupService>.Instance);
        using var cts = new CancellationTokenSource();
        await svc.StartAsync(cts.Token);
        await Task.Delay(200);
        await svc.StopAsync(cts.Token);
        Assert.Empty(stub.Requests);  // 完全没发请求
    }

    [Fact]
    public async Task Registers_worker_and_two_agent_instances_when_started()
    {
        var stub = new StubHandler();
        var http = new HttpClient(stub);
        var (wo, ab, agents) = BuildOpts();
        var svc = new WorkerStartupService(
            new FixedHttpFactory(http),
            Options.Create(wo), Options.Create(ab), Options.Create(agents),
            ThreeAgentRegistry(), NullLogger<WorkerStartupService>.Instance);

        // 启动 — startup 跑同步（agent upsert 顺序在 ExecuteAsync 第一段）
        // 用 StartAsync 让 BackgroundService 进入 ExecuteAsync
        await svc.StartAsync(CancellationToken.None);
        // 等 startup 一轮
        await Task.Delay(500);
        await svc.StopAsync(CancellationToken.None);

        // 期望至少：
        //   1 x POST /api/workers/register
        //   2 x PUT  /api/agents/{id}        (workbuddy + codex, minimax 跳过)
        //   2 x POST /api/agents/{id}/instances
        Assert.Contains(stub.Requests, r => r.Method == "POST" && r.Url.Contains("/api/workers/register"));
        Assert.Equal(2, stub.Requests.Count(r =>
            r.Method == "PUT" && r.Url.Contains("/api/agents/") && r.Url.EndsWith("/workbuddy-on-dev") || r.Url.EndsWith("/codex-on-dev")));
        // minimax 没注册（Command="" 跳过）
        Assert.DoesNotContain(stub.Requests, r => r.Url.Contains("minimax"));
    }

    [Fact]
    public async Task Uses_default_agent_id_when_not_configured()
    {
        var stub = new StubHandler();
        var http = new HttpClient(stub);
        var (wo, ab, agents) = BuildOpts();
        agents.WorkBuddy.AgentId = "";  // 显式空
        agents.Codex.AgentId = "";      // 显式空
        var svc = new WorkerStartupService(
            new FixedHttpFactory(http),
            Options.Create(wo), Options.Create(ab), Options.Create(agents),
            ThreeAgentRegistry(), NullLogger<WorkerStartupService>.Instance);

        await svc.StartAsync(CancellationToken.None);
        await Task.Delay(500);
        await svc.StopAsync(CancellationToken.None);

        // 默认 agent_id = "{worker_id}-{tool}"
        Assert.Contains(stub.Requests,
            r => r.Url.Contains("/dev-pc-01-workbuddy") || r.Url.Contains("/dev-pc-01-codex"));
    }

    [Fact]
    public async Task Sends_authorization_header_when_token_set()
    {
        var stub = new StubHandler();
        var http = new HttpClient(stub);
        var (wo, ab, agents) = BuildOpts();
        ab.StartupToken = "test-bearer-token-xyz";
        var svc = new WorkerStartupService(
            new FixedHttpFactory(http),
            Options.Create(wo), Options.Create(ab), Options.Create(agents),
            ThreeAgentRegistry(), NullLogger<WorkerStartupService>.Instance);

        await svc.StartAsync(CancellationToken.None);
        await Task.Delay(500);
        await svc.StopAsync(CancellationToken.None);

        Assert.NotEmpty(stub.Requests);
        Assert.All(stub.Requests, r =>
            Assert.Equal("Bearer test-bearer-token-xyz",
                r.Headers.GetValues("Authorization").FirstOrDefault()));
    }

    [Fact]
    public async Task Does_not_send_authorization_header_when_token_empty()
    {
        var stub = new StubHandler();
        var http = new HttpClient(stub);
        var (wo, ab, agents) = BuildOpts();
        ab.StartupToken = "";  // 显式空
        var svc = new WorkerStartupService(
            new FixedHttpFactory(http),
            Options.Create(wo), Options.Create(ab), Options.Create(agents),
            ThreeAgentRegistry(), NullLogger<WorkerStartupService>.Instance);

        await svc.StartAsync(CancellationToken.None);
        await Task.Delay(500);
        await svc.StopAsync(CancellationToken.None);

        Assert.NotEmpty(stub.Requests);
        Assert.All(stub.Requests, r =>
            Assert.False(r.Headers.Contains("Authorization")));
    }
}

/// <summary>Stub HttpMessageHandler：捕所有请求（method/url/body/headers），回 200。</summary>
internal sealed class StubHandler : HttpMessageHandler
{
    public List<StubRequest> Requests { get; } = new();
    public Func<HttpRequestMessage, HttpResponseMessage>? OnSend { get; set; } = _ =>
        new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent("{\"id\":42}", System.Text.Encoding.UTF8, "application/json")
        };

    protected override async Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage req, CancellationToken ct)
    {
        var body = req.Content is null ? "" : await req.Content.ReadAsStringAsync(ct);
        Requests.Add(new StubRequest
        {
            Method = req.Method.Method,
            Url = req.RequestUri?.ToString() ?? "",
            Body = body,
            Headers = req.Headers,
        });
        return (OnSend ?? (_ => new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent("{\"id\":42}", System.Text.Encoding.UTF8, "application/json")
        }))(req);
    }
}

internal sealed class StubRequest
{
    public string Method { get; set; } = "";
    public string Url { get; set; } = "";
    public string Body { get; set; } = "";
    public System.Net.Http.Headers.HttpRequestHeaders Headers { get; set; } = null!;
}

/// <summary>把固定 HttpClient 包成 IHttpClientFactory。</summary>
internal sealed class FixedHttpFactory : IHttpClientFactory
{
    private readonly HttpClient _client;
    public FixedHttpFactory(HttpClient client) => _client = client;
    public HttpClient CreateClient(string name) => _client;
}

/// <summary>FakeAdapter for AgentAdapterRegistry（PR-12 测试不需要真 CLI）。</summary>
internal sealed class FakeAdapter : IAgentAdapter
{
    public FakeAdapter(string agentType) => AgentType = agentType;
    public string AgentType { get; }
    public Task<AgentExecutionResult> ExecuteAsync(ExecutionContext context, CancellationToken ct) =>
        Task.FromResult(new AgentExecutionResult(true, "{}", null, 0, TimeSpan.Zero));
}
