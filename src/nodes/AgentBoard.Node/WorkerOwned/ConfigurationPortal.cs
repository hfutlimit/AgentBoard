using System.Net;
using System.Net.Http.Headers;
using System.Text.Json;
using Microsoft.Extensions.Options;

namespace AgentBoard.Node.WorkerOwned;

public static class ConfigurationPortal
{
    // Local UI needs no secret. Host/origin checks prevent a remote website
    // (including DNS rebinding) from turning the loopback portal into an API.
    internal static bool IsLocalRequest(HttpContext http)
    {
        var host = http.Request.Host.Host.Trim('[', ']');
        var origin = http.Request.Headers.Origin.ToString();
        var localHost = host.Equals("localhost", StringComparison.OrdinalIgnoreCase)
            || (IPAddress.TryParse(host, out var address) && IPAddress.IsLoopback(address));
        var readOnly = HttpMethods.IsGet(http.Request.Method) || HttpMethods.IsHead(http.Request.Method);
        return http.Connection.RemoteIpAddress is { } peer && IPAddress.IsLoopback(peer)
            && localHost
            && (origin.Length == 0 || origin == $"{http.Request.Scheme}://{http.Request.Host}")
            && (readOnly || http.Request.Headers["X-AgentBoard-Local-Portal"] == "1");
    }

    public static string Html { get; } = ReadPage();
    private static string ReadPage()
    {
        using var stream = typeof(ConfigurationPortal).Assembly.GetManifestResourceStream("AgentBoard.Node.WorkerOwned.ConfigurationPortal.html")!;
        using var reader = new StreamReader(stream);
        return reader.ReadToEnd();
    }

    public static void Map(WebApplication app, LocalConfigurationStore store, bool configurationOnly)
    {
        var activeRevision = store.Read().Revision;
        var group = app.MapGroup("/api/local");
        // No Portal Key: trusted local access only, never a remote admin API.
        group.AddEndpointFilter(async (context, next) =>
        {
            var http = context.HttpContext;
            if (!IsLocalRequest(http))
                return Results.StatusCode(403);
            http.Response.Headers.CacheControl = "no-store";
            return await next(context);
        });
        group.MapGet("/configuration", () => Results.Ok(store.Read()));
        group.MapPost("/agents", (CreateLocalAgentRequest request) =>
        {
            try { return Results.Ok(store.AddAgent(request)); }
            catch (ConfigurationConflictException) { return Results.Conflict(new { detail = "配置已变化，请重新加载后添加。" }); }
            catch (Exception e) when (e is InvalidOperationException or ArgumentException)
            { return Results.BadRequest(new { detail = e.Message }); }
        });
        group.MapPut("/configuration", (ConfigurationSnapshot request) =>
        {
            try { return Results.Ok(store.Save(request)); }
            catch (ConfigurationConflictException) { return Results.Conflict(new { detail = "配置已被其他页面修改，请重新加载后编辑。" }); }
            catch (Exception e) when (e is InvalidOperationException or ArgumentException or NullReferenceException)
            { return Results.BadRequest(new { detail = e is NullReferenceException ? "配置字段不能为空" : e.Message }); }
        });
        group.MapGet("/status", (IOptions<AgentBoardOptions> api, IOptions<RabbitMqOptions> rabbit, WorkerState state) =>
            Results.Ok(new
            {
                workerId = state.WorkerId, configurationOnly,
                restartRequired = store.Read().Revision != activeRevision,
                configPath = store.FilePath,
                serverUrl = Uri.TryCreate(api.Value.ServerUrl, UriKind.Absolute, out var server) ? server.GetLeftPart(UriPartial.Authority) : "",
                apiCredentialConfigured = !string.IsNullOrWhiteSpace(api.Value.StartupToken),
                brokerConfigured = Uri.TryCreate(rabbit.Value.Uri, UriKind.Absolute, out var mq) && mq.Scheme is "amqp" or "amqps",
                brokerHost = mq?.Host,
            }));
        group.MapGet("/projects", async (IHttpClientFactory factory, IOptions<AgentBoardOptions> options, CancellationToken ct) =>
        {
            var api = options.Value;
            if (!Uri.TryCreate(api.ServerUrl, UriKind.Absolute, out var server) || string.IsNullOrWhiteSpace(api.StartupToken))
                return Results.Problem("生产 API 地址或环境凭据未配置", statusCode: 503);
            using var client = factory.CreateClient();
            client.Timeout = TimeSpan.FromSeconds(20);
            client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", api.StartupToken);
            try
            {
                var root = server.AbsoluteUri.TrimEnd('/');
                using var identity = await client.GetAsync(root + "/api/auth/me", ct);
                if (!identity.IsSuccessStatusCode)
                    return Results.Problem($"生产身份校验返回 {(int)identity.StatusCode}", statusCode: 502);
                var projects = new List<object>();
                var offset = 0;
                while (true)
                {
                    using var response = await client.GetAsync($"{root}/api/projects?limit=200&offset={offset}", ct);
                    if (!response.IsSuccessStatusCode)
                        return Results.Problem($"生产 API 返回 {(int)response.StatusCode}", statusCode: 502);
                    using var page = JsonDocument.Parse(await response.Content.ReadAsStringAsync(ct));
                    var items = page.RootElement.GetProperty("items");
                    foreach (var item in items.EnumerateArray())
                        projects.Add(new { id = item.GetProperty("id").GetInt32(), name = item.GetProperty("name").GetString() });
                    offset += items.GetArrayLength();
                    if (items.GetArrayLength() == 0 || offset >= page.RootElement.GetProperty("total").GetInt32()) break;
                    if (offset > 20000) return Results.Problem("项目列表过大，请限制账户项目范围", statusCode: 502);
                }
                return Results.Ok(new { items = projects, total = projects.Count });
            }
            catch (Exception e) when (e is HttpRequestException or TaskCanceledException or JsonException or KeyNotFoundException)
            { return Results.Problem("无法连接生产 API，请检查连接及环境凭据", statusCode: 502); }
        });
    }
}
