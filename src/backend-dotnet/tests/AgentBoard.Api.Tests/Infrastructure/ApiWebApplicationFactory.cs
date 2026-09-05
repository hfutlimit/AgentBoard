// SPDX-License-Identifier: MIT
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using AgentBoard.Api.Durable;
using AgentBoard.Domain.Entities;
using AgentBoard.Infrastructure.Persistence;
using AgentBoard.Infrastructure.Persistence.Interceptors;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Infrastructure;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Hosting;

namespace AgentBoard.Api.Tests.Infrastructure;

/// <summary>
/// Gives each API test class an isolated database so xUnit can start
/// multiple application hosts in parallel without racing on SQLite schema creation.
/// </summary>
public sealed class ApiWebApplicationFactory : WebApplicationFactory<Program>
{
    private readonly bool _durableEnabled;
    private readonly string _databasePath = Path.Combine(
        Path.GetTempPath(),
        $"agentboard-api-tests-{Guid.NewGuid():N}.db");
    private readonly string _durableDatabasePath = Path.Combine(
        Path.GetTempPath(),
        $"agentboard-durable-api-tests-{Guid.NewGuid():N}.db");

    public ApiWebApplicationFactory() : this(false) { }

    private ApiWebApplicationFactory(bool durableEnabled) =>
        _durableEnabled = durableEnabled;

    public static ApiWebApplicationFactory CreateDurable() => new(true);

    public HttpStatusCode? DependencyReadStatus { get; set; }

    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        // "Testing" tells Serilog/OpenTelemetry to skip the file sink and
        // console-exporter overhead — keeps the per-test host lean.
        builder.UseEnvironment("Testing");

        builder.ConfigureAppConfiguration((_, configuration) =>
            configuration.AddInMemoryCollection(new Dictionary<string, string?>
            {
                ["DurableWorkflow:Enabled"] = _durableEnabled.ToString(),
                ["DurableWorkflow:DatabasePath"] = _durableDatabasePath,
                ["DurableWorkflow:RabbitMqUri"] = "amqp://guest:guest@localhost:5672/",
                // A non-placeholder credential so DatabaseAgentSelector and
                // WorkflowWorkContextResolver proceed to the transport instead
                // of failing closed; the in-proc stub handler below answers the
                // call, so no live FastAPI or real secret is involved.
                ["AgentBoard:FastApi:InternalToken"] = "test-internal-service-token",
            }));

        builder.ConfigureServices(services =>
        {
            if (_durableEnabled)
            {
                var durableWorkers = services
                    .Where(descriptor => descriptor.ServiceType == typeof(IHostedService)
                        && descriptor.ImplementationType is not null
                        && (descriptor.ImplementationType == typeof(DurableServerOutboxService)
                            || descriptor.ImplementationType == typeof(DurableServerResultConsumerService)))
                    .ToList();
                foreach (var descriptor in durableWorkers) services.Remove(descriptor);
            }

            services.RemoveAll<DbContextOptions<AppDbContext>>();
            services.RemoveAll<IDbContextOptionsConfiguration<AppDbContext>>();
            services.AddDbContext<AppDbContext>((serviceProvider, options) =>
            {
                options.UseSqlite($"Data Source={_databasePath}");
                options.AddInterceptors(
                    serviceProvider.GetRequiredService<AuditFieldsInterceptor>(),
                    serviceProvider.GetRequiredService<SoftDeleteInterceptor>(),
                    serviceProvider.GetRequiredService<DomainEventDispatcherInterceptor>());
            });

            // Route every outbound "AgentBoardFastApi" call to an in-proc fake
            // backed by the same per-test database. This lets the credential-
            // interop path (AuthMiddleware -> /api/auth/introspect) and the
            // durable control plane (resolver task read + agent-select) be
            // exercised end-to-end without a live FastAPI or MariaDB.
            services
                .AddHttpClient("AgentBoardFastApi")
                .ConfigurePrimaryHttpMessageHandler(serviceProvider =>
                    new MiniFastApiHandler(serviceProvider.GetRequiredService<IServiceScopeFactory>(),
                        () => DependencyReadStatus));
        });
    }

    protected override void Dispose(bool disposing)
    {
        base.Dispose(disposing);
        if (!disposing)
        {
            return;
        }

        // SQLite e_sqlite3 sometimes holds the file handle via mmap until
        // GC finalizers run, so a plain File.Delete on Windows can race and
        // fail with IOException. Best-effort retry with a short delay keeps
        // xUnit's Test Class Cleanup quiet without masking real failures.
        for (var attempt = 0; attempt < 5; attempt++)
        {
            try
            {
                foreach (var databasePath in new[] { _databasePath, _durableDatabasePath })
                {
                    File.Delete(databasePath);
                    File.Delete(databasePath + "-wal");
                    File.Delete(databasePath + "-shm");
                }
                return;
            }
            catch (IOException) when (attempt < 4)
            {
                Thread.Sleep(50);
            }
            catch
            {
                // Give up silently — temp file will be reaped by the OS.
                return;
            }
        }
    }

    /// <summary>
    /// A minimal in-proc stand-in for the FastAPI business API, backed by the
    /// same per-test <see cref="AppDbContext"/>. It answers only the endpoints the
    /// BFF calls over HTTP — credential introspection, task reads/dependencies,
    /// and durable agent selection — so those integration paths (the abk_
    /// credential interop and the durable resolver/selector) can be exercised
    /// without a live FastAPI or MariaDB. Unmodelled paths fail closed with 404,
    /// matching an unreachable upstream.
    /// </summary>
    private sealed class MiniFastApiHandler : HttpMessageHandler
    {
        private static readonly TimeSpan HeartbeatTtl = TimeSpan.FromMinutes(5);
        private readonly IServiceScopeFactory _scopeFactory;
        private readonly Func<HttpStatusCode?> _dependencyReadStatus;

        public MiniFastApiHandler(IServiceScopeFactory scopeFactory, Func<HttpStatusCode?> dependencyReadStatus)
        {
            _scopeFactory = scopeFactory;
            _dependencyReadStatus = dependencyReadStatus;
        }

        protected override Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request, CancellationToken cancellationToken)
        {
            var path = request.RequestUri?.AbsolutePath ?? string.Empty;
            var method = request.Method;

            HttpResponseMessage response;
            if (method == HttpMethod.Post && path == "/api/durable/materialize")
            {
                response = Ok(new JsonObject { ["completed_request_ids"] = new JsonArray() });
            }
            else if (method == HttpMethod.Get && path == "/api/durable/ready-tasks")
            {
                response = ReadyTasks(request.RequestUri!);
            }
            else if (method == HttpMethod.Get
                && path.EndsWith("/api/auth/introspect", StringComparison.OrdinalIgnoreCase))
            {
                response = Introspect(request);
            }
            else if (method == HttpMethod.Get
                && System.Text.RegularExpressions.Regex.IsMatch(path, @"/api/tasks/\d+/dependencies$"))
            {
                response = _dependencyReadStatus() is { } failure
                    ? new HttpResponseMessage(failure)
                    : ReadDependencies(path);
            }
            else if (method == HttpMethod.Get
                && System.Text.RegularExpressions.Regex.IsMatch(path, @"/api/tasks/\d+$"))
            {
                response = ReadTask(path);
            }
            else if (method == HttpMethod.Post
                && path.EndsWith("/api/durable/agent-select", StringComparison.OrdinalIgnoreCase))
            {
                response = AgentSelect(request);
            }
            else
            {
                response = new HttpResponseMessage(HttpStatusCode.NotFound);
            }

            return Task.FromResult(response);
        }

        private static HttpResponseMessage Ok(JsonNode node) => new(HttpStatusCode.OK)
        {
            Content = new StringContent(node.ToJsonString(), Encoding.UTF8, "application/json"),
        };

        private static HttpResponseMessage Unauthorized() => new(HttpStatusCode.Unauthorized);

        private HttpResponseMessage Introspect(HttpRequestMessage request)
        {
            var raw = request.Headers.Authorization?.Parameter;
            if (string.IsNullOrEmpty(raw) || !raw.StartsWith("abk_", StringComparison.Ordinal))
                return Unauthorized();

            using var scope = _scopeFactory.CreateScope();
            var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
            var hash = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(raw))).ToLowerInvariant();
            var key = db.ApiKeys.FirstOrDefault(k => k.KeyHash == hash && k.Enabled);
            if (key is null) return Unauthorized();

            var user = db.Users.FirstOrDefault(u => u.Id == key.UserId);
            var permissions = new JsonArray();
            foreach (var permission in DeserializeScopes(key.Scopes))
                permissions.Add(JsonValue.Create(permission));

            return Ok(new JsonObject
            {
                ["id"] = key.UserId,
                ["username"] = user?.Username,
                ["is_admin"] = user?.IsAdmin ?? false,
                ["auth_scheme"] = "api_key",
                ["permissions"] = permissions,
                ["api_key_id"] = key.Id,
                ["api_key_prefix"] = key.KeyPrefix,
                ["agent_ref"] = null,
            });
        }

        private HttpResponseMessage ReadyTasks(Uri uri)
        {
            var query = Microsoft.AspNetCore.WebUtilities.QueryHelpers.ParseQuery(uri.Query);
            var projectId = int.Parse(query["project_id"]!);
            var afterId = int.Parse(query["after_id"]!);
            using var scope = _scopeFactory.CreateScope();
            var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
            var items = new JsonArray();
            foreach (var task in db.Tasks.Where(t => t.ProjectId == projectId && t.Id > afterId
                         && t.Status == "todo" && t.CurrentAssignmentId == null).OrderBy(t => t.Id).ToList())
            {
                var deps = db.TaskDependencies.Where(d => d.TaskId == task.Id && d.DependencyType == "blocks").ToList();
                if (deps.Any(d => db.Tasks.FirstOrDefault(t => t.Id == d.DependsOnId)?.Status != "done")) continue;
                var ids = new JsonArray();
                foreach (var dep in deps) ids.Add(dep.DependsOnId);
                items.Add(new JsonObject
                {
                    ["id"] = task.Id, ["type"] = task.Type, ["story_id"] = task.StoryId,
                    ["dependency_ids"] = ids,
                    ["context"] = new JsonObject { ["title"] = task.Title, ["spec"] = task.Spec },
                });
            }
            return Ok(new JsonObject { ["items"] = items, ["next_after_id"] = 0 });
        }

        private HttpResponseMessage ReadDependencies(string path)
        {
            var taskId = int.Parse(System.Text.RegularExpressions.Regex.Match(path, @"/api/tasks/(\d+)/dependencies$").Groups[1].Value);
            using var scope = _scopeFactory.CreateScope();
            var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
            // Mirror the real FastAPI service, including reverse edges and missing tasks.
            JsonObject Entry(TaskDependency edge, int relatedId)
            {
                var task = db.Tasks.FirstOrDefault(t => t.Id == relatedId);
                return new JsonObject
                {
                    ["id"] = edge.Id,
                    ["task_id"] = relatedId,
                    ["type"] = edge.DependencyType,
                    ["task"] = task is null ? null : new JsonObject
                    {
                        ["id"] = task.Id,
                        ["status"] = task.Status,
                    },
                };
            }
            var blockers = new JsonArray();
            foreach (var edge in db.TaskDependencies.Where(d => d.TaskId == taskId).ToList())
                blockers.Add(Entry(edge, edge.DependsOnId));
            var reverse = new JsonArray();
            foreach (var edge in db.TaskDependencies.Where(d => d.DependsOnId == taskId).ToList())
                reverse.Add(Entry(edge, edge.TaskId));
            return Ok(new JsonObject { ["blockers"] = blockers, ["blocked_by"] = reverse });
        }

        private HttpResponseMessage ReadTask(string path)
        {
            var rawId = System.Text.RegularExpressions.Regex.Match(path, @"/api/tasks/(\d+)$").Groups[1].Value;
            if (!int.TryParse(rawId, out var taskId)) return Unauthorized();

            using var scope = _scopeFactory.CreateScope();
            var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
            var task = db.Tasks.FirstOrDefault(t => t.Id == taskId);
            if (task is null) return new HttpResponseMessage(HttpStatusCode.NotFound);

            return Ok(new JsonObject
            {
                ["id"] = task.Id,
                ["status"] = task.Status,
                ["owner_user_id"] = task.OwnerUserId.HasValue
                    ? JsonValue.Create(task.OwnerUserId.Value)
                    : null,
                ["project_id"] = task.ProjectId,
                ["needed_capabilities"] = task.NeededCapabilities,
                ["type"] = task.Type,
            });
        }

        private HttpResponseMessage AgentSelect(HttpRequestMessage request)
        {
            var body = request.Content?.ReadAsStringAsync().GetAwaiter().GetResult() ?? "{}";
            JsonNode? payload;
            try
            {
                payload = JsonNode.Parse(body);
            }
            catch (JsonException)
            {
                return Unauthorized();
            }

            payload ??= new JsonObject();
            var projectId = payload["project_id"]?.GetValue<int>() ?? 0;
            var ownerUserId = payload["owner_user_id"]?.GetValue<int>() ?? 0;

            var exclude = new HashSet<string>(StringComparer.Ordinal);
            if (payload["exclude"] is JsonArray excludeArray)
            {
                foreach (var item in excludeArray)
                {
                    if (item?.GetValueKind() == JsonValueKind.String)
                        exclude.Add(item.GetValue<string>());
                }
            }

            var required = new Dictionary<string, double>(StringComparer.OrdinalIgnoreCase);
            if (payload["capabilities"] is JsonArray capArray)
            {
                foreach (var item in capArray)
                {
                    if (item is null) continue;
                    if (item is JsonValue)
                    {
                        var scalar = NormalizeName(item.GetValue<string>());
                        if (scalar is not null)
                            required[scalar] = Math.Max(1, required.GetValueOrDefault(scalar));
                        continue;
                    }

                    var name = NormalizeName(item["name"]?.GetValue<string>());
                    var minimum = item["minimum_level"]?.GetValue<double>() ?? 1;
                    if (name is not null)
                        required[name] = Math.Max(minimum, required.GetValueOrDefault(name));
                }
            }

            using var scope = _scopeFactory.CreateScope();
            var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
            var cutoff = DateTime.UtcNow - HeartbeatTtl;

            var candidates =
                from agent in db.Agents
                join instance in db.AgentInstances on agent.AgentId equals instance.AgentId
                join worker in db.Workers on instance.WorkerId equals worker.WorkerId
                join mapping in db.WorkerProjectMappings on instance.WorkerId equals mapping.WorkerId
                where agent.Enabled && agent.Online && agent.UserId == ownerUserId
                    && instance.Enabled && instance.Online
                    && instance.LastHeartbeat != null && instance.LastHeartbeat >= cutoff
                    && worker.Status == "active"
                    && worker.LastHeartbeat != null && worker.LastHeartbeat >= cutoff
                    && mapping.ProjectId == projectId && mapping.Enabled
                orderby agent.Id, instance.WorkerId
                select new { Agent = agent, Instance = instance };

            foreach (var candidate in candidates.ToList())
            {
                if (exclude.Contains(candidate.Agent.AgentId)) continue;
                var profile = ParseCapabilityProfile(candidate.Agent.Capabilities);
                if (profile is null) continue;
                var covers = true;
                foreach (var pair in required)
                {
                    if (!profile.TryGetValue(pair.Key, out var level) || level < pair.Value)
                    {
                        covers = false;
                        break;
                    }
                }

                if (!covers) continue;

                var capabilities = new JsonArray();
                foreach (var name in profile.Keys.OrderBy(k => k, StringComparer.OrdinalIgnoreCase))
                    capabilities.Add(JsonValue.Create(name));

                return Ok(new JsonObject
                {
                    ["selection"] = new JsonObject
                    {
                        ["worker_id"] = candidate.Instance.WorkerId,
                        ["agent_id"] = candidate.Agent.AgentId,
                        ["capabilities"] = capabilities,
                        ["provider_id"] = candidate.Instance.ExecutorType,
                    },
                });
            }

            return Ok(new JsonObject { ["selection"] = null, ["reason"] = "no-eligible-agent" });
        }

        private static IReadOnlyList<string> DeserializeScopes(string json)
        {
            try
            {
                using var doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(json) ? "[]" : json);
                if (doc.RootElement.ValueKind != JsonValueKind.Array) return Array.Empty<string>();
                return doc.RootElement.EnumerateArray()
                    .Where(e => e.ValueKind == JsonValueKind.String)
                    .Select(e => e.GetString()!)
                    .ToList();
            }
            catch (JsonException)
            {
                return Array.Empty<string>();
            }
        }

        private static IReadOnlyDictionary<string, double>? ParseCapabilityProfile(string json)
        {
            var result = new Dictionary<string, double>(StringComparer.OrdinalIgnoreCase);
            try
            {
                using var doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(json) ? "[]" : json);
                if (doc.RootElement.ValueKind != JsonValueKind.Array) return null;
                foreach (var item in doc.RootElement.EnumerateArray())
                {
                    string? name;
                    double level;
                    if (item.ValueKind == JsonValueKind.String)
                    {
                        name = NormalizeName(item.GetString());
                        level = 3;
                    }
                    else if (item.ValueKind == JsonValueKind.Object)
                    {
                        name = NormalizeName(
                            item.TryGetProperty("name", out var n) && n.ValueKind == JsonValueKind.String
                                ? n.GetString()
                                : null);
                        level = item.TryGetProperty("level", out var l) && l.ValueKind == JsonValueKind.Number
                            ? l.GetDouble()
                            : 3;
                    }
                    else
                    {
                        return null;
                    }

                    if (name is null) return null;
                    result[name] = Math.Max(level, result.GetValueOrDefault(name));
                }
            }
            catch (JsonException)
            {
                return null;
            }

            return result;
        }

        private static string? NormalizeName(string? value)
        {
            var trimmed = value?.Trim().ToLowerInvariant();
            return string.IsNullOrWhiteSpace(trimmed) ? null : trimmed;
        }
    }
}
