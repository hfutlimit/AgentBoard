// SPDX-License-Identifier: MIT
using System.Linq;
using System.Net;
using System.Text.Json;
using AgentBoard.Node;
using AgentBoard.Node.Agents;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Xunit;

namespace AgentBoard.Node.Tests;

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

    private static (NodeOptions wo, AgentBoardOptions ab, AgentsOptions agents) BuildOpts(
        string serverUrl = "http://test:8080",
        string workerId = "dev-pc-01")
    {
        var wo = new NodeOptions { Id = workerId, HeartbeatSeconds = 5 };
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
        //   2 x POST /api/agents/register   (workbuddy + codex, minimax 跳过)
        //   2 x POST /api/agents/{id}/instances
        Assert.Contains(stub.Requests, r => r.Method == "POST" && r.Url.Contains("/api/workers/register"));
        Assert.Equal(2, stub.Requests.Count(r =>
            r.Method == "POST" && r.Url.EndsWith("/api/agents/register")
            && (r.Body?.Contains("workbuddy-on-dev") == true
                || r.Body?.Contains("codex-on-dev") == true)));
        var registrations = stub.Requests.Where(r =>
            r.Method == "POST" && r.Url.EndsWith("/api/agents/register")).ToList();
        Assert.All(registrations, r =>
        {
            using var json = System.Text.Json.JsonDocument.Parse(r.Body);
            Assert.Equal("[]", json.RootElement.GetProperty("roles").GetString());
        });
        var instances = stub.Requests.Where(r =>
            r.Method == "POST" && r.Url.Contains("/instances")).ToList();
        Assert.Equal(2, instances.Count);
        Assert.Contains(instances, r =>
            System.Text.Json.JsonDocument.Parse(r.Body).RootElement
                .GetProperty("executor_type").GetString() == "workbuddy");
        Assert.Contains(instances, r =>
            System.Text.Json.JsonDocument.Parse(r.Body).RootElement
                .GetProperty("executor_type").GetString() == "codex");
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

    // ---------- P0-1 (2026-09-01)：per-agent identity ----------

    [Fact]
    public async Task Uses_per_agent_token_for_agent_registration_and_shared_token_for_worker()
    {
        var stub = new StubHandler();
        var http = new HttpClient(stub);
        var (wo, ab, agents) = BuildOpts();
        ab.StartupToken = "global-token";
        agents.WorkBuddy.AgentBoardToken = "wb-token";
        agents.Codex.AgentBoardToken = "codex-token";
        var svc = new WorkerStartupService(
            new FixedHttpFactory(http),
            Options.Create(wo), Options.Create(ab), Options.Create(agents),
            ThreeAgentRegistry(), NullLogger<WorkerStartupService>.Instance);

        await svc.StartAsync(CancellationToken.None);
        await Task.Delay(500);
        await svc.StopAsync(CancellationToken.None);

        // worker 注册用全局 StartupToken
        var workerReg = Assert.Single(stub.Requests,
            r => r.Method == "POST" && r.Url.Contains("/api/workers/register"));
        Assert.Equal("Bearer global-token",
            workerReg.Headers.GetValues("Authorization").First());

        // agent 注册/instance 用各自 token —— reviewer isolation 依赖
        // 同一 worker 上的不同 agent 有不同 user_id
        var wbReg = stub.Requests.Single(r =>
            r.Url.EndsWith("/api/agents/register")
            && r.Body.Contains("workbuddy-on-dev"));
        Assert.Equal("Bearer wb-token",
            wbReg.Headers.GetValues("Authorization").First());
        var codexReg = stub.Requests.Single(r =>
            r.Url.EndsWith("/api/agents/register")
            && r.Body.Contains("codex-on-dev"));
        Assert.Equal("Bearer codex-token",
            codexReg.Headers.GetValues("Authorization").First());

        var instances = stub.Requests.Where(r => r.Url.Contains("/instances")).ToList();
        Assert.Equal(2, instances.Count);
        var wbInst = instances.Single(r =>
            System.Text.Json.JsonDocument.Parse(r.Body).RootElement
                .GetProperty("executor_type").GetString() == "workbuddy");
        Assert.Equal("Bearer wb-token",
            wbInst.Headers.GetValues("Authorization").First());
        var codexInst = instances.Single(r =>
            System.Text.Json.JsonDocument.Parse(r.Body).RootElement
                .GetProperty("executor_type").GetString() == "codex");
        Assert.Equal("Bearer codex-token",
            codexInst.Headers.GetValues("Authorization").First());
    }

    [Fact]
    public async Task Per_agent_token_empty_falls_back_to_shared_startup_token()
    {
        var stub = new StubHandler();
        var http = new HttpClient(stub);
        var (wo, ab, agents) = BuildOpts();
        ab.StartupToken = "global-token";
        // 两个 agent 都不配 AgentBoardToken → 回退全局（向后兼容）
        var svc = new WorkerStartupService(
            new FixedHttpFactory(http),
            Options.Create(wo), Options.Create(ab), Options.Create(agents),
            ThreeAgentRegistry(), NullLogger<WorkerStartupService>.Instance);

        await svc.StartAsync(CancellationToken.None);
        await Task.Delay(500);
        await svc.StopAsync(CancellationToken.None);

        Assert.NotEmpty(stub.Requests);
        Assert.All(stub.Requests, r =>
            Assert.Equal("Bearer global-token",
                r.Headers.GetValues("Authorization").FirstOrDefault()));
    }

    // ---------- P0-3 (2026-09-01)：RequireRegistration fail-fast ----------
    // 断言面：svc.StartupFailure（.NET 10 BackgroundService.StartAsync 吞掉
    // 异常且可能延后调度 ExecuteAsync；生产由 Generic Host StopHost 兑现
    // fail-fast）。单测轮询等待 StartupFailure 落定，不依赖调度时机。

    private static async Task<Exception?> WaitForStartupFailureAsync(
        WorkerStartupService svc, int timeoutMs = 5000)
    {
        var deadline = DateTime.UtcNow.AddMilliseconds(timeoutMs);
        while (svc.StartupFailure is null && DateTime.UtcNow < deadline)
        {
            await Task.Delay(20);
        }
        return svc.StartupFailure;
    }

    [Fact]
    public async Task Require_registration_fails_fast_when_server_url_empty()
    {
        var stub = new StubHandler();
        var http = new HttpClient(stub);
        var (wo, ab, agents) = BuildOpts(serverUrl: "");
        ab.RequireRegistration = true;
        var svc = new WorkerStartupService(
            new FixedHttpFactory(http),
            Options.Create(wo), Options.Create(ab), Options.Create(agents),
            ThreeAgentRegistry(), NullLogger<WorkerStartupService>.Instance);

        // 不再静默跳过：直接抛错，让 first-run 配置错误在启动时暴露
        await svc.StartAsync(CancellationToken.None);
        var ex = await WaitForStartupFailureAsync(svc);
        Assert.IsType<InvalidOperationException>(ex);
        Assert.Contains("ServerUrl", ex!.Message);
        Assert.Empty(stub.Requests);
    }

    [Fact]
    public async Task Require_registration_fails_fast_when_rabbitmq_uri_empty()
    {
        var stub = new StubHandler();
        var http = new HttpClient(stub);
        var (wo, ab, agents) = BuildOpts();
        ab.RequireRegistration = true;
        var svc = new WorkerStartupService(
            new FixedHttpFactory(http),
            Options.Create(wo), Options.Create(ab), Options.Create(agents),
            ThreeAgentRegistry(), NullLogger<WorkerStartupService>.Instance,
            Options.Create(new RabbitMqOptions { Uri = "" }));

        await svc.StartAsync(CancellationToken.None);
        var ex = await WaitForStartupFailureAsync(svc);
        Assert.IsType<InvalidOperationException>(ex);
        Assert.Contains("RabbitMq:Uri", ex!.Message);
        Assert.Empty(stub.Requests);
    }

    [Fact]
    public async Task Require_registration_fails_fast_when_no_workbuddy_or_codex_registered()
    {
        var stub = new StubHandler();
        var http = new HttpClient(stub);
        var (wo, ab, agents) = BuildOpts();
        ab.RequireRegistration = true;
        // 典型踩法：Command 留空指望 CliLocator 自动发现 —— scheduling 层
        // 根本看不到 agent，Story 永远 todo。现在启动时直接报错。
        agents.WorkBuddy.Command = "";
        agents.Codex.Command = "";
        var svc = new WorkerStartupService(
            new FixedHttpFactory(http),
            Options.Create(wo), Options.Create(ab), Options.Create(agents),
            ThreeAgentRegistry(), NullLogger<WorkerStartupService>.Instance,
            // broker 正常配置，把 RabbitMQ 检查和 agent 注册检查解耦
            Options.Create(new RabbitMqOptions { Uri = "amqp://test" }));

        await svc.StartAsync(CancellationToken.None);
        var ex = await WaitForStartupFailureAsync(svc);
        Assert.IsType<InvalidOperationException>(ex);
        Assert.Contains("no WorkBuddy/Codex agent was registered", ex!.Message);
        Assert.Contains("Agents:WorkBuddy:Command is empty", ex.Message);
        // worker 本身注册了，但没有任何 agent
        Assert.Contains(stub.Requests, r => r.Url.Contains("/api/workers/register"));
        Assert.DoesNotContain(stub.Requests, r => r.Url.EndsWith("/api/agents/register"));
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
