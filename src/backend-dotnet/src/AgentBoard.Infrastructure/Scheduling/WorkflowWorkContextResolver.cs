// SPDX-License-Identifier: MIT
using System.Net.Http.Headers;
using System.Text.Json;
using AgentBoard.Application.Abstractions;
using AgentBoard.Contracts;
using AgentBoard.Domain.Workflow.Durable;
using Microsoft.Extensions.Configuration;

namespace AgentBoard.Infrastructure.Scheduling;

/// <summary>
/// Resolves a business task into the durable execution context by reading the
/// FastAPI business database over HTTP (using the internal service credential),
/// rather than the local SQLite shadow. The .NET BFF has no live MySQL provider
/// (Pomelo has no EF Core 10 release), so FastAPI is the single source of truth
/// for tasks/dependencies — this mirrors the write path in
/// <c>FastApiTaskProjectionClient</c>.
/// </summary>
public sealed class WorkflowWorkContextResolver : IWorkflowWorkContextResolver
{
    private readonly IHttpClientFactory _clients;
    private readonly IConfiguration _configuration;

    public WorkflowWorkContextResolver(
        IHttpClientFactory clients,
        IConfiguration configuration)
    {
        _clients = clients;
        _configuration = configuration;
    }

    public async Task<WorkflowWorkResolution> ResolveTaskAsync(
        int taskId,
        string workspaceId,
        string baseVersion,
        string taskContext,
        CancellationToken cancellationToken = default)
    {
        var client = _clients.CreateClient("AgentBoardFastApi");

        using var taskResponse = await SendAuthorizedAsync(
            client, HttpMethod.Get, $"api/tasks/{taskId}", cancellationToken);
        if (!taskResponse.IsSuccessStatusCode)
        {
            // 404 (missing) or any non-success (e.g. 403 cross-tenant, 401
            // upstream auth) fails closed as NotFound so the durable plane
            // never starts a run it cannot legitimately observe.
            return new WorkflowWorkResolution(WorkflowWorkResolutionStatus.NotFound, null);
        }

        using var taskDoc = JsonDocument.Parse(
            await taskResponse.Content.ReadAsStringAsync(cancellationToken));
        var task = taskDoc.RootElement;

        var status = GetString(task, "status");
        var ownerUserId = GetNullableInt(task, "owner_user_id");
        if (ownerUserId is null)
        {
            return new WorkflowWorkResolution(
                WorkflowWorkResolutionStatus.MissingOwner, null, status);
        }

        var projectId = GetNullableInt(task, "project_id") ?? 0;
        var neededCapabilities = GetString(task, "needed_capabilities") ?? "[]";
        var type = GetString(task, "type");

        var blockingIds = await ResolveBlockingTaskIdsAsync(
            taskId, cancellationToken);
        if (blockingIds is null)
        {
            return new WorkflowWorkResolution(
                WorkflowWorkResolutionStatus.DependenciesUnavailable, null, status);
        }
        if (blockingIds.Count > 0)
        {
            return new WorkflowWorkResolution(
                WorkflowWorkResolutionStatus.DependenciesNotReady,
                null,
                status,
                blockingIds);
        }

        var context = new WorkflowWorkContext(
            projectId,
            "task",
            taskId,
            ownerUserId.Value,
            new WorkspaceReference(projectId.ToString(), workspaceId, baseVersion),
            taskContext,
            AgentCapabilityJson.ParseRequirements(neededCapabilities),
            type);

        return new WorkflowWorkResolution(
            WorkflowWorkResolutionStatus.Found,
            context,
            status,
            Array.Empty<int>());
    }

    private async Task<IReadOnlyList<int>?> ResolveBlockingTaskIdsAsync(
        int taskId, CancellationToken cancellationToken)
    {
        try
        {
            var client = _clients.CreateClient("AgentBoardFastApi");
            using var response = await SendAuthorizedAsync(
                client, HttpMethod.Get, $"api/tasks/{taskId}/dependencies", cancellationToken);
            if (!response.IsSuccessStatusCode) return null;

            using var doc = JsonDocument.Parse(
                await response.Content.ReadAsStringAsync(cancellationToken));
            // FastAPI /dependencies: blockers are prerequisites; blocked_by
            // contains reverse dependents. Do not confuse this with /readiness.
            if (doc.RootElement.ValueKind != JsonValueKind.Object
                || !doc.RootElement.TryGetProperty("blockers", out var blockers)
                || blockers.ValueKind != JsonValueKind.Array) return null;

            var result = new HashSet<int>();
            foreach (var entry in blockers.EnumerateArray())
            {
                if (entry.ValueKind != JsonValueKind.Object) return null;
                var dependencyType = GetString(entry, "type");
                // Same policy as FastAPI get_task_readiness: only blocks gates execution.
                if (dependencyType is "relates_to" or "blocked_by") continue;
                if (dependencyType != "blocks") return null;

                var blockerId = GetNullableInt(entry, "task_id");
                if (blockerId is null or <= 0
                    || !entry.TryGetProperty("task", out var task)) return null;
                if (task.ValueKind == JsonValueKind.Null)
                {
                    // A deleted/missing prerequisite is not complete.
                    result.Add(blockerId.Value);
                    continue;
                }
                if (task.ValueKind != JsonValueKind.Object
                    || GetNullableInt(task, "id") != blockerId) return null;
                var status = GetString(task, "status");
                if (string.IsNullOrWhiteSpace(status)) return null;
                if (status != "done") result.Add(blockerId.Value);
            }
            return result.Order().ToArray();
        }
        catch (HttpRequestException) { return null; }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            return null; // Upstream timeout, not caller cancellation.
        }
        catch (JsonException) { return null; }
    }

    private async Task<HttpResponseMessage> SendAuthorizedAsync(
        HttpClient client, HttpMethod method, string path, CancellationToken cancellationToken)
    {
        using var request = new HttpRequestMessage(method, path);
        var token = _configuration["AgentBoard:FastApi:InternalToken"];
        if (!string.IsNullOrWhiteSpace(token)
            && !string.Equals(token, "REPLACE_WITH_INTERNAL_SERVICE_TOKEN", StringComparison.Ordinal))
        {
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        }
        return await client.SendAsync(request, cancellationToken);
    }

    private static string? GetString(JsonElement element, string property)
    {
        if (!element.TryGetProperty(property, out var value) || value.ValueKind == JsonValueKind.Null)
            return null;
        return value.ValueKind == JsonValueKind.String ? value.GetString() : value.ToString();
    }

    private static int? GetNullableInt(JsonElement element, string property)
    {
        if (!element.TryGetProperty(property, out var value) || value.ValueKind == JsonValueKind.Null)
            return null;
        return value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var parsed)
            ? parsed
            : int.TryParse(value.ToString(), out var fallback) ? fallback : null;
    }
}
