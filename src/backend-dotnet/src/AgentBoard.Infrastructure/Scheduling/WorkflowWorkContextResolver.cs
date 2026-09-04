// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Contracts;
using AgentBoard.Domain.Workflow.Durable;

namespace AgentBoard.Infrastructure.Scheduling;

public sealed class WorkflowWorkContextResolver : IWorkflowWorkContextResolver
{
    private readonly ITaskItemRepository _tasks;

    public WorkflowWorkContextResolver(ITaskItemRepository tasks) => _tasks = tasks;

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

        return new WorkflowWorkResolution(
            WorkflowWorkResolutionStatus.Found,
            new WorkflowWorkContext(
                task.ProjectId,
                "task",
                task.Id,
                task.OwnerUserId.Value,
                new WorkspaceReference(task.ProjectId.ToString(), workspaceId, baseVersion),
                taskContext,
                AgentCapabilityJson.ParseRequirements(task.NeededCapabilities)));
    }
}
