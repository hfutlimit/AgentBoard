// SPDX-License-Identifier: MIT
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using AgentBoard.Domain.Workflow.Durable;
using Microsoft.Extensions.Options;

namespace AgentBoard.Api.Durable;

public sealed class FastApiTaskProjectionClient
{
    private readonly IHttpClientFactory _clients;
    private readonly IConfiguration _configuration;

    public FastApiTaskProjectionClient(IHttpClientFactory clients, IConfiguration configuration)
    {
        _clients = clients;
        _configuration = configuration;
    }

    public async Task ProjectAsync(TaskStatusProjection projection, CancellationToken cancellationToken)
    {
        var client = _clients.CreateClient("AgentBoardFastApi");
        using var get = CreateRequest(HttpMethod.Get, $"api/tasks/{projection.TaskId}");
        using var currentResponse = await client.SendAsync(get, cancellationToken);
        currentResponse.EnsureSuccessStatusCode();
        using var current = JsonDocument.Parse(await currentResponse.Content.ReadAsStringAsync(cancellationToken));
        var root = current.RootElement;
        var currentStatus = root.GetProperty("status").GetString();
        var storyId = root.TryGetProperty("story_id", out var story) && story.ValueKind == JsonValueKind.Number
            ? story.GetInt32()
            : (int?)null;

        if (!string.Equals(currentStatus, projection.TargetStatus, StringComparison.OrdinalIgnoreCase))
        {
            using var put = CreateRequest(HttpMethod.Put, $"api/tasks/{projection.TaskId}/status");
            put.Content = JsonContent.Create(new Dictionary<string, object?>
            {
                ["status"] = projection.TargetStatus,
                ["status_reason"] = projection.StatusReason,
                ["reason"] = projection.Reason,
            });
            using var updated = await client.SendAsync(put, cancellationToken);
            updated.EnsureSuccessStatusCode();
        }

        if (string.Equals(projection.TargetStatus, "done", StringComparison.OrdinalIgnoreCase) && storyId is not null)
        {
            await CompleteStoryWhenReadyAsync(client, storyId.Value, cancellationToken);
        }
    }

    private async Task CompleteStoryWhenReadyAsync(HttpClient client, int storyId, CancellationToken cancellationToken)
    {
        using var list = CreateRequest(HttpMethod.Get, $"api/stories/{storyId}/tasks?limit=200");
        using var listResponse = await client.SendAsync(list, cancellationToken);
        listResponse.EnsureSuccessStatusCode();
        using var document = JsonDocument.Parse(await listResponse.Content.ReadAsStringAsync(cancellationToken));
        var root = document.RootElement;
        var items = root.ValueKind == JsonValueKind.Object && root.TryGetProperty("items", out var nested)
            ? nested
            : root;
        if (items.ValueKind != JsonValueKind.Array
            || items.EnumerateArray().Any(item =>
                !item.TryGetProperty("status", out var status)
                || !string.Equals(status.GetString(), "done", StringComparison.OrdinalIgnoreCase)))
        {
            return;
        }

        using var complete = CreateRequest(HttpMethod.Post, $"api/stories/{storyId}/complete");
        using var response = await client.SendAsync(complete, cancellationToken);
        response.EnsureSuccessStatusCode();
    }

    private HttpRequestMessage CreateRequest(HttpMethod method, string path)
    {
        var request = new HttpRequestMessage(method, path);
        var token = _configuration["AgentBoard:FastApi:InternalToken"];
        if (!string.IsNullOrWhiteSpace(token)
            && !string.Equals(token, "REPLACE_WITH_INTERNAL_SERVICE_TOKEN", StringComparison.Ordinal))
        {
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        }
        return request;
    }
}

public sealed class DurableTaskProjectionService : BackgroundService
{
    private readonly DurableWorkflowOptions _options;
    private readonly DurableServerRuntime _runtime;
    private readonly FastApiTaskProjectionClient _client;
    private readonly ILogger<DurableTaskProjectionService> _log;

    public DurableTaskProjectionService(
        IOptions<DurableWorkflowOptions> options,
        DurableServerRuntime runtime,
        FastApiTaskProjectionClient client,
        ILogger<DurableTaskProjectionService> log)
    {
        _options = options.Value;
        _runtime = runtime;
        _client = client;
        _log = log;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        if (!_options.Enabled) return;

        using var timer = new PeriodicTimer(TimeSpan.FromMilliseconds(500));
        while (await timer.WaitForNextTickAsync(stoppingToken))
        {
            var projection = _runtime.PrepareTaskProjection();
            if (projection is null) continue;

            try
            {
                await _client.ProjectAsync(projection, stoppingToken);
                _runtime.CompleteTaskProjection(projection.ProjectionId);
                _log.LogInformation(
                    "Projected durable run {RunId} to task {TaskId} status {Status}",
                    projection.RunId, projection.TaskId, projection.TargetStatus);
            }
            catch (Exception error) when (!stoppingToken.IsCancellationRequested)
            {
                var delay = TimeSpan.FromSeconds(Math.Min(30, 1 << Math.Min(projection.Attempts, 5)));
                _runtime.RetryTaskProjection(
                    projection.ProjectionId,
                    $"{error.GetType().Name}: {error.Message}",
                    delay);
                _log.LogError(
                    error,
                    "Task projection {ProjectionId} failed; retrying in {Delay}",
                    projection.ProjectionId, delay);
            }
        }
    }
}
