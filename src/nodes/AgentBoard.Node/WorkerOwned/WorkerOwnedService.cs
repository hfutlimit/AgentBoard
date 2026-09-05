// SPDX-License-Identifier: MIT
using System.Net;
using System.Net.Http.Headers;
using System.Text.Json;
using System.Text.Json.Nodes;
using AgentBoard.Contracts;
using Microsoft.Extensions.Options;
using RabbitMQ.Client;

namespace AgentBoard.Node.WorkerOwned;

/// <summary>
/// Local orchestration and competing-consumer execution. No Worker-specific
/// queue and no broad broadcast subscription. Only configured project/kinds.
/// </summary>
public sealed class WorkerOwnedService : BackgroundService
{
    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web)
        { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
    private readonly WorkerOwnedOptions _options;
    private readonly AgentBoardOptions _api;
    private readonly RabbitMqOptions _rabbit;
    private readonly NodeOptions _node;
    private readonly LocalAdapterFactory _adapters;
    private readonly ILogger<WorkerOwnedService> _log;
    private readonly IHttpClientFactory _http;
    private readonly WorkerState _state;
    private string WorkerId => _state.WorkerId;
    private WorkJournal _journal = null!;
    private readonly Dictionary<string, long> _instances = new(StringComparer.Ordinal);
    private DateTimeOffset _lastPresence;

    public WorkerOwnedService(IOptions<WorkerOwnedOptions> options, IOptions<AgentBoardOptions> api,
        IOptions<RabbitMqOptions> rabbit, IOptions<NodeOptions> node, LocalAdapterFactory adapters,
        IHttpClientFactory http, ILogger<WorkerOwnedService> log, WorkerState state)
    {
        _options = options.Value; _api = api.Value; _rabbit = rabbit.Value;
        _node = node.Value; _adapters = adapters; _http = http; _log = log; _state = state;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        if (!_options.Enabled) return;
        _options.Validate();
        if (string.IsNullOrWhiteSpace(_api.ServerUrl) || string.IsNullOrWhiteSpace(_api.StartupToken)
            || !Uri.TryCreate(_rabbit.Uri, UriKind.Absolute, out _))
            throw new InvalidOperationException("WorkerOwned requires the business API, credential and RabbitMQ");
        _journal = new WorkJournal(_node.HistoryDatabasePath,
            new Uri(_api.ServerUrl.TrimEnd('/') + "/").AbsoluteUri + "|" + WorkerId);
        // Two processes must not share a Worker journal and replay the same
        // claim token into two physical executions.
        using var processLock = new FileStream(Path.GetFullPath(_node.HistoryDatabasePath) + ".worker-owned.lock",
            FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
        // Refuse to consume against an older Server which cannot fence discussion turns.
        using (var preflight = Client())
        {
            using var response = await preflight.GetAsync($"api/worker-work/snapshot?project_id={_options.Projects[0].ProjectId}&entity_type=task&limit=1", stoppingToken);
            response.EnsureSuccessStatusCode();
            using var protocol = JsonDocument.Parse(await response.Content.ReadAsStringAsync(stoppingToken));
            if (!protocol.RootElement.TryGetProperty("protocol", out var version)
                || version.GetString() != "worker-work.discussions.v1")
                throw new InvalidOperationException("Deploy the discussion-capable FastAPI/MCP migration before starting this Worker");
        }
        await Register(stoppingToken);
        using var lifetime = CancellationTokenSource.CreateLinkedTokenSource(stoppingToken);
        var reconciliation = ReconcileLoop(lifetime.Token);
        try
        {
            while (!stoppingToken.IsCancellationRequested)
            {
                try { await Consume(lifetime.Token); }
                catch (Exception error) when (!stoppingToken.IsCancellationRequested)
                {
                    _log.LogWarning("Worker-owned broker interrupted ({Error}); reconnecting", error.GetType().Name);
                    await Task.Delay(TimeSpan.FromSeconds(5), stoppingToken);
                }
            }
        }
        finally
        {
            lifetime.Cancel();
            try { await reconciliation; } catch (OperationCanceledException) { }
        }
    }

    private HttpClient Client(LocalAgentProfile? agent = null)
    {
        var client = _http.CreateClient();
        client.BaseAddress = new Uri(_api.ServerUrl.TrimEnd('/') + "/");
        client.Timeout = TimeSpan.FromSeconds(30);
        var token = string.IsNullOrWhiteSpace(agent?.Runtime.AgentBoardToken)
            ? _api.StartupToken : agent.Runtime.AgentBoardToken;
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
        return client;
    }

    private static Task<HttpResponseMessage> Post(HttpClient client, string path, object body, CancellationToken ct) =>
        client.PostAsJsonAsync(path, body, Json, ct);

    private async Task Register(CancellationToken ct)
    {
        using var client = Client();
        using var worker = await Post(client, "api/workers/register", new
            { worker_id = WorkerId, hostname = Environment.MachineName, status = "active" }, ct);
        worker.EnsureSuccessStatusCode();
        foreach (var agent in _options.EnabledAgents)
        {
            using var agentClient = Client(agent);
            using var registration = await Post(agentClient, "api/agents/register", new
            {
                agent_id = agent.Id, name = $"{agent.Id} on {WorkerId}", roles = "[]",
                model = agent.Runtime.Model, cli_command = Path.GetFileName(agent.Runtime.Command),
                capabilities = agent.WorkKinds.Select(name => new { name, level = 1 }),
            }, ct);
            registration.EnsureSuccessStatusCode();
            using var instance = await Post(agentClient, $"api/agents/{Uri.EscapeDataString(agent.Id)}/instances", new
            {
                worker_id = WorkerId, executor_type = agent.Provider, model = agent.Runtime.Model,
                cli_command = Path.GetFileName(agent.Runtime.Command), enabled = true,
            }, ct);
            instance.EnsureSuccessStatusCode();
            using var registrationResult = JsonDocument.Parse(await instance.Content.ReadAsStringAsync(ct));
            _instances[agent.Id] = registrationResult.RootElement.GetProperty("id").GetInt64();
        }
        _log.LogInformation("Worker-owned local profiles registered: {Agents}", string.Join(", ", _options.Agents.Select(a => a.Id)));
    }

    private async Task ReconcileLoop(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            try
            {
                using var client = Client();
                if (DateTimeOffset.UtcNow - _lastPresence > TimeSpan.FromSeconds(15))
                {
                    _state.LastHeartbeatAttemptAt = DateTimeOffset.UtcNow;
                    foreach (var profile in _options.EnabledAgents)
                    {
                        using var presenceClient = Client(profile);
                        using var presence = await Post(presenceClient,
                            $"api/workers/{Uri.EscapeDataString(WorkerId)}/agent-instances/{_instances[profile.Id]}/heartbeat",
                            new { probe_ok = true, probe_message = "Worker-owned runtime connected" }, ct);
                        presence.EnsureSuccessStatusCode();
                    }
                    _lastPresence = DateTimeOffset.UtcNow;
                    _state.LastHeartbeatSuccessAt = _lastPresence;
                }
                foreach (var project in _options.Projects)
                {
                    if (_state.Paused) break;
                    var tasks = new List<JsonElement>();
                    foreach (var entityType in new[] { "proposal", "task" })
                    {
                        var cursor = 0;
                        do
                        {
                            using var page = await client.GetAsync($"api/worker-work/snapshot?project_id={project.ProjectId}&entity_type={entityType}&after_id={cursor}", ct);
                            page.EnsureSuccessStatusCode();
                            using var json = JsonDocument.Parse(await page.Content.ReadAsStringAsync(ct));
                            foreach (var item in json.RootElement.GetProperty("items").EnumerateArray())
                            {
                                if (entityType == "task") tasks.Add(item.Clone());
                                var offer = WorkPlanner.Next(project.ProjectId, entityType, item);
                                if (offer is null) continue;
                                using var response = await Post(client, "api/worker-work/offers", offer, ct);
                                if (response.StatusCode != HttpStatusCode.Conflict) response.EnsureSuccessStatusCode();
                            }
                            cursor = json.RootElement.GetProperty("next_after_id").GetInt32();
                        } while (cursor > 0 && !ct.IsCancellationRequested);
                    }
                    // This closure decision is also Worker-owned. Server
                    // independently verifies every child before applying it.
                    foreach (var group in tasks.Where(t => t.GetProperty("story_id").ValueKind == JsonValueKind.Number)
                        .GroupBy(t => t.GetProperty("story_id").GetInt32()))
                    {
                        if (group.All(t => t.GetProperty("status").GetString() == "done")
                            && group.First().GetProperty("story_status").GetString() is not ("done" or "blocked" or "backlog"))
                        {
                            using var closed = await Post(client, $"api/worker-work/stories/{group.Key}/complete", new { }, ct);
                            if (closed.StatusCode != HttpStatusCode.Conflict) closed.EnsureSuccessStatusCode();
                        }
                    }
                }
            }
            catch (Exception error) when (!ct.IsCancellationRequested)
            {
                _log.LogWarning("Worker reconciliation deferred ({Error}): {Message}", error.GetType().Name, error.Message);
            }
            await Task.Delay(TimeSpan.FromSeconds(Math.Max(2, _options.ReconcileSeconds)), ct);
        }
    }

    private async Task Consume(CancellationToken ct)
    {
        var factory = new ConnectionFactory { Uri = new Uri(_rabbit.Uri), AutomaticRecoveryEnabled = false };
        using var connection = factory.CreateConnection();
        using var channel = connection.CreateModel();
        channel.ExchangeDeclare(WorkerWorkKinds.Exchange, ExchangeType.Direct, durable: true);
        channel.ExchangeDeclare(WorkerWorkKinds.Exchange + ".dlx", ExchangeType.Fanout, durable: true);
        var subscriptions = _options.Subscriptions().Select(s => (s.ProjectId, s.Kind, Target: (string?)null))
            .Concat(_options.Projects.SelectMany(p => _options.EnabledAgents.SelectMany(a =>
                a.WorkKinds.Select(k => (p.ProjectId, k, Target: (string?)a.Id))))).Distinct().ToArray();
        static string Queue(int project, string kind, string? target) => target is null
            ? WorkerWorkKinds.Queue(project, kind) : WorkerWorkKinds.AgentQueue(project, kind, target);
        foreach (var (project, kind, target) in subscriptions)
        {
            var queue = Queue(project, kind, target);
            channel.QueueDeclare(queue, durable: true, exclusive: false, autoDelete: false,
                arguments: new Dictionary<string, object> { ["x-dead-letter-exchange"] = WorkerWorkKinds.Exchange + ".dlx" });
            channel.QueueBind(queue, WorkerWorkKinds.Exchange, queue);
        }
        // Pull one unacked message at a time. This avoids prefetch stealing
        // seven queues worth of work while this Worker has only one free slot.
        while (!ct.IsCancellationRequested && connection.IsOpen)
        {
            var received = false;
            foreach (var (project, kind, target) in subscriptions)
            {
                if (_state.Paused) break;
                var delivery = channel.BasicGet(Queue(project, kind, target), autoAck: false);
                if (delivery is null) continue;
                received = true;
                bool ack;
                try
                {
                    using var message = JsonDocument.Parse(delivery.Body);
                    var root = message.RootElement;
                    if (root.GetProperty("schema").GetString() != "worker-work.v2"
                        || root.GetProperty("project_id").GetInt32() != project || root.GetProperty("kind").GetString() != kind
                        || (root.TryGetProperty("target_agent", out var recipient) ? recipient.GetString() : null) != target)
                        throw new InvalidDataException("Misrouted work envelope");
                    ack = await Execute(root.GetProperty("work_id").GetInt64(), project, kind, target, ct);
                }
                catch (Exception error) when (error is JsonException or InvalidDataException or KeyNotFoundException)
                {
                    channel.BasicReject(delivery.DeliveryTag, requeue: false);
                    _log.LogError("Invalid Worker work envelope sent to DLQ");
                    continue;
                }
                if (ack) channel.BasicAck(delivery.DeliveryTag, multiple: false);
                else
                {
                    channel.BasicNack(delivery.DeliveryTag, multiple: false, requeue: true);
                    await Task.Delay(TimeSpan.FromSeconds(2), ct);
                }
            }
            if (!received) await Task.Delay(TimeSpan.FromSeconds(1), ct);
        }
    }

    private async Task<bool> Execute(long workId, int project, string kind, string? target, CancellationToken ct)
    {
        var saved = _journal.Get(workId);
        foreach (var profile in _options.Candidates(project, kind).Where(a => target is null || a.Id == target)
            .OrderBy(a => a.Id == saved?.AgentId ? 0 : 1))
        {
            var entry = saved?.AgentId == profile.Id ? saved : new JournalEntry(workId, profile.Id, Guid.NewGuid().ToString("N"), null);
            _journal.Save(entry);
            using var client = Client(profile);
            var lease = new { project_id = project, kind, worker_id = WorkerId, agent_id = profile.Id, token = entry.Token };
            using var claim = await Post(client, $"api/worker-work/{workId}/claim", lease, ct);
            if (claim.StatusCode == HttpStatusCode.Forbidden) continue; // dynamic self-review/QA exclusion
            if (claim.StatusCode == HttpStatusCode.Conflict)
            {
                var reason = await claim.Content.ReadAsStringAsync(ct);
                if (reason.Contains("new_token_required", StringComparison.Ordinal)) _journal.Remove(workId);
                using var status = await client.GetAsync($"api/worker-work/{workId}", ct);
                status.EnsureSuccessStatusCode();
                using var state = JsonDocument.Parse(await status.Content.ReadAsStringAsync(ct));
                return state.RootElement.GetProperty("state").GetString() is "completed" or "failed";
            }
            claim.EnsureSuccessStatusCode();
            using var accepted = JsonDocument.Parse(await claim.Content.ReadAsStringAsync(ct));
            if (accepted.RootElement.GetProperty("state").GetString() is "completed" or "failed") return true;
            using var running = CancellationTokenSource.CreateLinkedTokenSource(ct);
            var renewal = Renew(client, workId, lease, running);
            var active = new ActiveExecution(workId, $"worker-work:{workId}", kind,
                accepted.RootElement.GetProperty("work").GetProperty("entity_id").GetInt64(), profile.Id, DateTimeOffset.UtcNow);
            _state.Begin(active);
            try
            {
                if (entry.Result is null)
                {
                    var workspace = _options.Projects.Single(p => p.ProjectId == project).LocalPath;
                    using var workspaceLock = await LockWorkspace(workspace, running.Token);
                    var context = accepted.RootElement.GetProperty("context").GetRawText();
                    var before = await GitHead(workspace, running.Token);
                    var beforeStatus = await Git(workspace, ["status", "--porcelain"], running.Token);
                    var businessContext = accepted.RootElement.GetProperty("context");
                    if (businessContext.TryGetProperty("evidence", out var evidence))
                    {
                        foreach (var record in evidence.EnumerateArray())
                        {
                            if (record.GetProperty("result").TryGetProperty("commit", out var commit)
                                && commit.ValueKind == JsonValueKind.String && !string.IsNullOrWhiteSpace(commit.GetString()))
                                await Git(workspace, ["merge-base", "--is-ancestor", commit.GetString()!, "HEAD"], running.Token);
                        }
                    }
                    var adapter = _adapters.Create(profile);
                    _log.LogInformation("Work {WorkId} {Kind} starts on {Agent} ({Provider}/{Model})", workId, kind, profile.Id, profile.Provider, profile.Runtime.Model);
                    var result = await adapter.ExecuteAsync(new ExecutionContext(workId, $"worker-work:{workId}",
                        kind, accepted.RootElement.GetProperty("work").GetProperty("entity_id").GetInt64(),
                        accepted.RootElement.GetProperty("work").GetProperty("iteration").GetInt32(),
                        profile.Provider, context, WorkPlanner.Prompt(kind, context, profile), WorkingDirectory: workspace,
                        WorkerOwnedExecution: true), running.Token);
                    running.Token.ThrowIfCancellationRequested();
                    if (!result.Success || string.IsNullOrWhiteSpace(result.OutputJson))
                        throw new InvalidOperationException(result.ErrorMessage ?? "Provider returned no structured business result");
                    _state.SetAgentReport(profile.Id, Agents.AgentReadiness.AllOk());
                    _state.IncrementAgentTotal(profile.Id);
                    var output = JsonNode.Parse(result.OutputJson)?.AsObject() ?? throw new InvalidDataException("Missing result object");
                    var discussion = WorkPlanner.IsDiscussion(businessContext);
                    if (!discussion) WorkPlanner.ValidateResult(kind, output);
                    if (kind == "proposal" && output["decision"]?.GetValue<string>() == "finalize"
                        && output["create_ticket"]?.GetValue<bool>() == true)
                    {
                        var title = accepted.RootElement.GetProperty("context").GetProperty("item").GetProperty("title").GetString()!;
                        output["ticket_plan"] ??= WorkPlanner.TicketPlan(title, output["spec"]?.GetValue<string>()
                            ?? throw new InvalidDataException("Proposal requires a converged specification"));
                        output["activate_story"] = true;
                    }
                    WorkPlanner.AddQaFollowup(kind, output, businessContext);
                    var after = await GitHead(workspace, running.Token);
                    var afterStatus = await Git(workspace, ["status", "--porcelain"], running.Token);
                    if ((discussion || kind.EndsWith("_review", StringComparison.Ordinal) || kind is "qa" or "proposal")
                        && (before != after || beforeStatus != afterStatus))
                        throw new InvalidOperationException("Read-only work changed the repository");
                    if (kind is "design" or "dev" && afterStatus != beforeStatus)
                        throw new InvalidOperationException("Execution left uncommitted changes; evidence is not transferable");
                    output["commit"] = after;
                    output["agent_id"] = profile.Id;
                    output["provider"] = profile.Provider;
                    output["model"] = profile.Runtime.Model;
                    entry = entry with { Result = output.ToJsonString() };
                    _journal.Save(entry);
                }
                using var completion = await Post(client, $"api/worker-work/{workId}/complete", new
                { project_id = project, kind, worker_id = WorkerId, agent_id = profile.Id, token = entry.Token,
                  result = JsonSerializer.Deserialize<JsonElement>(entry.Result!) }, running.Token);
                if (completion.StatusCode is HttpStatusCode.UnprocessableEntity or HttpStatusCode.Conflict)
                    throw new InvalidDataException("Worker result validation failed: " + await completion.Content.ReadAsStringAsync(running.Token));
                completion.EnsureSuccessStatusCode();
                _state.LastError = null;
                _log.LogInformation("Work {WorkId} {Kind} completed by {Agent}", workId, kind, profile.Id);
                return true;
            }
            catch (Exception error) when (!ct.IsCancellationRequested && !running.IsCancellationRequested)
            {
                // A network/lease error keeps the saved result for replay;
                // never reinvoke a provider merely because completion timed out.
                if (error is HttpRequestException or TaskCanceledException) throw;
                _log.LogWarning("Work {WorkId} failed: {Error}", workId, error.Message);
                _state.LastError = error.Message;
                using var failed = await Post(client, $"api/worker-work/{workId}/fail", new
                { project_id = project, kind, worker_id = WorkerId, agent_id = profile.Id, token = entry.Token,
                  result = new { summary = error.Message[..Math.Min(2000, error.Message.Length)] } }, ct);
                failed.EnsureSuccessStatusCode();
                _journal.Remove(workId);
                return true; // Server persisted either the retry outbox or terminal failure.
            }
            finally
            {
                _state.End(active);
                running.Cancel();
                try { await renewal; } catch (OperationCanceledException) { }
            }
        }
        return false;
    }

    private static async Task Renew(HttpClient client, long id, object lease, CancellationTokenSource running)
    {
        try
        {
            while (!running.IsCancellationRequested)
            {
                await Task.Delay(TimeSpan.FromSeconds(30), running.Token);
                using var response = await Post(client, $"api/worker-work/{id}/heartbeat", lease, running.Token);
                response.EnsureSuccessStatusCode();
            }
        }
        catch (Exception) when (!running.IsCancellationRequested) { running.Cancel(); }
    }

    private static async Task<FileStream> LockWorkspace(string workspace, CancellationToken ct)
    {
        var key = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(
            System.Text.Encoding.UTF8.GetBytes(Path.GetFullPath(workspace).TrimEnd('\\', '/').ToUpperInvariant())));
        var path = Path.Combine(Path.GetTempPath(), $"agentboard-workspace-{key}.lock");
        while (true)
        {
            ct.ThrowIfCancellationRequested();
            try { return new FileStream(path, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None); }
            catch (IOException) { await Task.Delay(500, ct); }
        }
    }

    private static Task<string> GitHead(string directory, CancellationToken ct) => Git(directory, ["rev-parse", "HEAD"], ct);

    private static async Task<string> Git(string directory, string[] arguments, CancellationToken ct)
    {
        var info = new System.Diagnostics.ProcessStartInfo("git")
        { WorkingDirectory = directory, UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
        foreach (var argument in arguments) info.ArgumentList.Add(argument);
        using var process = System.Diagnostics.Process.Start(info) ?? throw new InvalidOperationException("Cannot inspect checkout");
        var output = await process.StandardOutput.ReadToEndAsync(ct);
        await process.WaitForExitAsync(ct);
        if (process.ExitCode != 0) throw new InvalidOperationException("Checkout is unavailable or does not contain the accepted upstream commit");
        return output.Trim();
    }
}
