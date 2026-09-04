// SPDX-License-Identifier: MIT
using AgentBoard.Api.Durable;
using AgentBoard.Application.Abstractions;
using AgentBoard.Contracts;
using AgentBoard.Domain.Workflow;
using AgentBoard.Domain.Workflow.Durable;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.DurableWorkflow;

[ApiController]
[Route("api/durable-workflows")]
[ServiceFilter(typeof(DurableWorkflowGateFilter))]
public sealed class DurableWorkflowController : ControllerBase
{
    private readonly DurableServerRuntime _runtime;
    private readonly IWorkflowWorkContextResolver _workContexts;

    public DurableWorkflowController(
        DurableServerRuntime runtime,
        IWorkflowWorkContextResolver workContexts)
    {
        _runtime = runtime;
        _workContexts = workContexts;
    }

    [HttpPost("versions")]
    public ActionResult<WorkflowVersion> PublishVersion([FromBody] WorkflowVersion version) =>
        Ok(_runtime.Mutate(plane => plane.Registry.PublishVersion(version)));

    [HttpPost("runs")]
    public async Task<ActionResult<WorkflowStartResult>> StartRun(
        [FromBody] StartRunRequest request,
        CancellationToken cancellationToken)
    {
        var resolution = await _workContexts.ResolveTaskAsync(
            request.TaskId,
            request.WorkspaceId,
            request.BaseVersion,
            request.TaskContext,
            cancellationToken);
        if (resolution.Status == WorkflowWorkResolutionStatus.NotFound) return NotFound();
        if (resolution.Status == WorkflowWorkResolutionStatus.MissingOwner)
        {
            return UnprocessableEntity(new ProblemDetails
            {
                Status = StatusCodes.Status422UnprocessableEntity,
                Title = "Task owner is required",
                Detail = $"Task {request.TaskId} has no owner_user_id; durable assignment fails closed.",
            });
        }
        if (resolution.Status == WorkflowWorkResolutionStatus.DependenciesNotReady)
        {
            return Conflict(new ProblemDetails
            {
                Status = StatusCodes.Status409Conflict,
                Title = "Task dependencies are not ready",
                Detail = $"Task {request.TaskId} is blocked by tasks: {string.Join(", ", resolution.BlockingTaskIds ?? Array.Empty<int>())}.",
            });
        }
        if (!string.Equals(resolution.CurrentStatus, "todo", StringComparison.OrdinalIgnoreCase))
        {
            return Conflict(new ProblemDetails
            {
                Status = StatusCodes.Status409Conflict,
                Title = "Task is not eligible for durable execution",
                Detail = $"Task {request.TaskId} is '{resolution.CurrentStatus}'; only todo tasks can start a durable workflow.",
            });
        }

        var started = _runtime.Mutate(plane =>
            plane.Orchestrator.Start(
                request.WorkflowRunId,
                request.WorkflowVersionId,
                resolution.Context!));
        return started.Assignment is null ? Accepted(started) : Ok(started);
    }

    [HttpPost("executions/{executionId}/cancel")]
    public IActionResult Cancel(string executionId, [FromBody] CancelRequest request)
    {
        _runtime.Mutate(plane => plane.Dispatcher.DispatchCancel(executionId, request.Reason));
        return Accepted();
    }

    [HttpGet("runs/{runId}")]
    public ActionResult<object> GetRun(string runId) =>
        _runtime.Read(plane => plane.Registry.Snapshot(runId)) is { } snapshot
            ? Ok(snapshot)
            : NotFound();

    [HttpGet("handoffs/{handoffId}")]
    public ActionResult<HandoffContext> GetHandoff(string handoffId) =>
        Ok(_runtime.Read(plane => plane.Handoffs.Require(handoffId)));

    [HttpPost("approvals")]
    public ActionResult<ApprovalRequest> RequestApproval([FromBody] RequestApprovalRequest request) => Ok(
        _runtime.Mutate(plane => plane.AwaitApproval(
            request.StageRunId, request.AssignmentId, request.Decision,
            TimeSpan.FromSeconds(request.WindowSeconds))));

    [HttpPost("approvals/{approvalId}/decision")]
    public ActionResult<object> DecideApproval(string approvalId, [FromBody] DecideApprovalRequest request) => Ok(
        _runtime.Mutate(plane =>
        {
            var stage = plane.ResolveApproval(
                approvalId, request.Granted, request.Actor, request.Reason);
            var approval = plane.Approvals.Require(approvalId);
            return new
            {
                stage,
                grant = approval.State == ApprovalState.Granted
                    ? plane.Approvals.Grant(approvalId)
                    : null,
            };
        }));

    [HttpGet("approvals/{approvalId}/grant")]
    public ActionResult<ApprovalGrant> GetApprovalGrant(string approvalId) =>
        Ok(_runtime.Read(plane => plane.Approvals.Grant(approvalId)));

    [HttpGet("operations")]
    public ActionResult<object> Operations() => Ok(_runtime.Read(plane => new
    {
        outbox = plane.Outbox.Messages.ToArray(),
        dead_letters = plane.DeadLetters.Entries.ToArray(),
        approvals = plane.Approvals.Requests.ToArray(),
        task_status_projections = plane.TaskProjections.Entries.ToArray(),
        audit = plane.Registry.Audit.Records.ToArray(),
    }));

}

public sealed record StartRunRequest(
    string WorkflowRunId,
    string WorkflowVersionId,
    int TaskId,
    string WorkspaceId,
    string BaseVersion,
    string TaskContext = "{}");
public sealed record CancelRequest(string Reason);
public sealed record RequestApprovalRequest(
    string StageRunId,
    string AssignmentId,
    PolicyDecisionRequest Decision,
    int WindowSeconds);
public sealed record DecideApprovalRequest(bool Granted, string Actor, string Reason);
