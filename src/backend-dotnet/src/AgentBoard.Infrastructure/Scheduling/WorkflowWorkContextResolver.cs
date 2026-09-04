// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Contracts;
using AgentBoard.Domain.Workflow.Durable;

namespace AgentBoard.Infrastructure.Scheduling;

public sealed class WorkflowWorkContextResolver : IWorkflowWorkContextResolver
{
    private readonly ITaskItemRepository _tasks;
    private readonly ITaskDependencyRepository _dependencies;

    public WorkflowWorkContextResolver(
        ITaskItemRepository tasks,
        ITaskDependencyRepository dependencies)
    {
        _tasks = tasks;
        _dependencies = dependencies;
    }

    public async Task<WorkflowWorkResolution> ResolveTaskAsync(
        int taskId,
        string workspaceId,
        string baseVersion,
        string taskContext,
        CancellationToken cancellationToken = default)
    {
        var task = await _tasks.GetByIdAsync(taskId, cancellationToken);
        if (task is null)
        {
            return new WorkflowWorkResolution(WorkflowWorkResolutionStatus.NotFound, null);
        }
        if (task.OwnerUserId is null)
        {
            return new WorkflowWorkResolution(WorkflowWorkResolutionStatus.MissingOwner, null);
        }

        var dependencies = await _dependencies.ListAsync(
            dependency => dependency.TaskId == taskId && dependency.DependencyType == "blocks",
            cancellationToken);
        if (dependencies.Count > 0)
        {
            var dependencyIds = dependencies.Select(dependency => dependency.DependsOnId).Distinct().ToArray();
            var blockers = await _tasks.ListAsync(
                candidate => dependencyIds.Contains(candidate.Id) && candidate.Status != "done",
                cancellationToken);
            if (blockers.Count > 0)
            {
                return new WorkflowWorkResolution(
                    WorkflowWorkResolutionStatus.DependenciesNotReady,
                    null,
                    task.Status,
                    blockers.Select(blocker => blocker.Id).Order().ToArray());
            }
        }

        return new WorkflowWorkResolution(
            WorkflowWorkResolutionStatus.Found,
            new WorkflowWorkContext(
                task.ProjectId,
                "task",
                task.Id,
                task.OwnerUserId.Value,
                new WorkspaceReference(task.ProjectId.ToString(), workspaceId, baseVersion),
                taskContext,
                AgentCapabilityJson.ParseRequirements(task.NeededCapabilities),
                task.Type),
            task.Status,
            Array.Empty<int>());
    }
}
