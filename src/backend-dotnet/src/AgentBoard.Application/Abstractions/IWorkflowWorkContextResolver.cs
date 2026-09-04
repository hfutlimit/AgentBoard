// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Workflow.Durable;

namespace AgentBoard.Application.Abstractions;

public enum WorkflowWorkResolutionStatus
{
    Found,
    NotFound,
    MissingOwner,
    DependenciesNotReady,
}

public sealed record WorkflowWorkResolution(
    WorkflowWorkResolutionStatus Status,
    WorkflowWorkContext? Context,
    string? CurrentStatus = null,
    IReadOnlyList<int>? BlockingTaskIds = null);

/// <summary>
/// Resolves a business work item into the immutable execution context used by
/// the durable workflow plane without exposing persistence to API controllers.
/// </summary>
public interface IWorkflowWorkContextResolver
{
    Task<WorkflowWorkResolution> ResolveTaskAsync(
        int taskId,
        string workspaceId,
        string baseVersion,
        string taskContext,
        CancellationToken cancellationToken = default);
}
