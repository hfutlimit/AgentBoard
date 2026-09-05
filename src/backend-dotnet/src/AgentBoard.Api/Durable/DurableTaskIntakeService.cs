using System.Net.Http.Headers;
using System.Text.Json;
using AgentBoard.Application.Abstractions;
using AgentBoard.Contracts;
using AgentBoard.Domain.Workflow.Durable;
using Microsoft.Extensions.Options;

namespace AgentBoard.Api.Durable;

public sealed class DurableIntakeOptions
{
    public bool Enabled { get; set; }
    public int PollSeconds { get; set; } = 5;
    public List<DurableIntakeProject> Projects { get; set; } = [];
}

public sealed class DurableIntakeProject
{
    public int ProjectId { get; set; }
    public string WorkspaceId { get; set; } = "";
    public string BaseVersion { get; set; } = "";
    // Published immutable versions chosen by the operator, keyed by task type.
    public Dictionary<string, string> WorkflowVersions { get; set; } = new();
}

/// <summary>
/// Durable business todo rows are the input backlog. Stable run ids and the
/// existing atomic plane commit close replay/crash windows without a second
/// scheduler or an HTTP call from inside the business DB transaction.
/// </summary>
public sealed class DurableTaskIntakeService(
    IOptions<DurableWorkflowOptions> options,
    DurableServerRuntime runtime,
    IHttpClientFactory clients,
    IServiceScopeFactory scopes,
    IConfiguration configuration,
    ILogger<DurableTaskIntakeService> log) : BackgroundService
{
    private readonly Dictionary<int, int> _cursors = new();
    private readonly SemaphoreSlim _scan = new(1, 1);

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        if (!options.Value.Enabled || !options.Value.Intake.Enabled) return;
        while (!stoppingToken.IsCancellationRequested)
        {
            await ScanOnceAsync(stoppingToken);
            await Task.Delay(TimeSpan.FromSeconds(Math.Max(1, options.Value.Intake.PollSeconds)), stoppingToken);
        }
    }

    public async Task ScanOnceAsync(CancellationToken cancellationToken)
    {
        if (!options.Value.Enabled || !options.Value.Intake.Enabled) return;
        await _scan.WaitAsync(cancellationToken);
        try
        {
            foreach (var project in options.Value.Intake.Projects)
            {
                try { await ScanProjectAsync(project, cancellationToken); }
                catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested) { throw; }
                catch (Exception error)
                {
                    // Do not log HTTP bodies/credentials or silently route to legacy.
                    log.LogError("Durable intake project {ProjectId} failed ({ErrorType}); retrying next scan",
                        project.ProjectId, error.GetType().Name);
                }
            }
        }
        finally { _scan.Release(); }
    }

    private async Task ScanProjectAsync(DurableIntakeProject project, CancellationToken ct)
    {
        if (project.ProjectId <= 0 || string.IsNullOrWhiteSpace(project.WorkspaceId)
            || string.IsNullOrWhiteSpace(project.BaseVersion) || project.WorkflowVersions.Count == 0)
            throw new InvalidOperationException("Intake requires explicit project/workspace/base/version bindings");

        using var materialized = await SendAsync(HttpMethod.Post,
            $"api/durable/materialize?project_id={project.ProjectId}", ct);
        materialized.EnsureSuccessStatusCode();
        using var response = await SendAsync(HttpMethod.Get,
            $"api/durable/ready-tasks?project_id={project.ProjectId}&after_id={_cursors.GetValueOrDefault(project.ProjectId)}&limit=50", ct);
        response.EnsureSuccessStatusCode();
        using var document = JsonDocument.Parse(await response.Content.ReadAsStringAsync(ct));
        foreach (var item in document.RootElement.GetProperty("items").EnumerateArray())
        {
            var taskId = item.GetProperty("id").GetInt32();
            try { await AcceptAsync(project, item, taskId, ct); }
            catch (OperationCanceledException) when (ct.IsCancellationRequested) { throw; }
            catch (Exception error)
            {
                log.LogWarning("Durable intake task {TaskId} deferred ({ErrorType})", taskId, error.GetType().Name);
            }
        }
        _cursors[project.ProjectId] = document.RootElement.GetProperty("next_after_id").GetInt32();
    }

    private async Task AcceptAsync(DurableIntakeProject project, JsonElement item, int taskId, CancellationToken ct)
    {
        var runId = $"business-task-{taskId}";
        // Check before status re-read: the previous commit may have succeeded
        // while its status projection/next poll was interrupted.
        if (runtime.Read(plane => plane.Registry.Snapshot(runId)) is not null) return;
        var taskType = item.GetProperty("type").GetString()!;
        if (!project.WorkflowVersions.TryGetValue(taskType, out var versionId))
            throw new InvalidOperationException("No published workflow binding for task type");

        var dependencies = item.GetProperty("dependency_ids").EnumerateArray().Select(id => id.GetInt32()).ToHashSet();
        var inherited = runtime.Read(plane =>
        {
            var previous = plane.Orchestration.Capture().Runs.Where(r =>
                r.Context.ProjectId == project.ProjectId && dependencies.Contains(r.Context.WorkItemId)).ToArray();
            var stages = previous.SelectMany(r => plane.Registry.RequireRun(r.RunId).Stages).ToArray();
            var developmentStages = stages.Where(s => s.Current.StageType == StageType.Development)
                .Select(s => s.Current.StageRunId).ToHashSet();
            var excluded = previous.SelectMany(r => r.Context.UpstreamDevelopmentAgents ?? [])
                .Concat(plane.Leases.Capture().Where(a => developmentStages.Contains(a.StageRunId)).Select(a => a.AgentId))
                .Distinct().ToArray();
            var executions = stages.SelectMany(s => s.Executions).Where(e => e.Outcome is not null).ToArray();
            var evidence = executions.Select(e => new
            {
                outcome = e.Outcome,
                result = e.Attempts.Single(a => a.Current.AttemptId == e.Outcome!.AcceptedAttemptId).Result,
                evidence = plane.Evidence.For(e.Outcome!.AcceptedAttemptId),
            }).ToArray();
            var baseVersion = executions.OrderBy(e => e.Outcome!.AcceptedAt)
                .Select(e => plane.Evidence.For(e.Outcome!.AcceptedAttemptId)?.CommitOrVersion)
                .LastOrDefault(value => !string.IsNullOrWhiteSpace(value)) ?? project.BaseVersion;
            return (excluded, evidence, baseVersion);
        });
        var contextJson = JsonSerializer.Serialize(new
        {
            task = item.GetProperty("context"),
            upstream_outcomes = inherited.evidence,
        }, ContractJson.Options);
        using var scope = scopes.CreateScope();
        var resolver = scope.ServiceProvider.GetRequiredService<IWorkflowWorkContextResolver>();
        var resolution = await resolver.ResolveTaskAsync(taskId, project.WorkspaceId, inherited.baseVersion, contextJson, ct);
        if (resolution.Status != WorkflowWorkResolutionStatus.Found || resolution.CurrentStatus != "todo") return;
        if (resolution.Context!.ProjectId != project.ProjectId) throw new InvalidOperationException("Project mismatch");
        var context = resolution.Context with { UpstreamDevelopmentAgents = inherited.excluded };
        var started = runtime.Mutate(plane =>
        {
            if (plane.Registry.Snapshot(runId) is not null) return false;
            // One active business task per shared checkout. Two Nodes may take
            // successive stages, but may not concurrently edit the same workspace.
            if (plane.Orchestration.Capture().Runs.Any(r =>
                r.Context.ProjectId == project.ProjectId && r.Context.Workspace.WorkspaceId == project.WorkspaceId
                && plane.Registry.RequireRun(r.RunId).Current.State is not
                    (WorkflowRunState.Succeeded or WorkflowRunState.Failed or WorkflowRunState.Cancelled))) return false;
            plane.Orchestrator.Start(runId, versionId, context);
            return true;
        });
        if (started) log.LogInformation("Durable intake accepted task {TaskId} as {RunId}", taskId, runId);
    }

    private async Task<HttpResponseMessage> SendAsync(HttpMethod method, string path, CancellationToken ct)
    {
        var token = configuration["AgentBoard:FastApi:InternalToken"];
        if (string.IsNullOrWhiteSpace(token) || token == "REPLACE_WITH_INTERNAL_SERVICE_TOKEN")
            throw new InvalidOperationException("Internal service credential required");
        using var request = new HttpRequestMessage(method, path);
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        return await clients.CreateClient("AgentBoardFastApi").SendAsync(request, ct);
    }
}
