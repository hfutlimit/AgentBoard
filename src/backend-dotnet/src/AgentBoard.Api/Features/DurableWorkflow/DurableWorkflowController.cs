// SPDX-License-Identifier: MIT
using AgentBoard.Api.Durable;
using AgentBoard.Contracts;
using AgentBoard.Domain.Workflow;
using AgentBoard.Domain.Workflow.Durable;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.DurableWorkflow;

[ApiController]
[Route("api/durable-workflows")]
public sealed class DurableWorkflowController : ControllerBase
{
    private readonly DurableServerRuntime _runtime;

    public DurableWorkflowController(DurableServerRuntime runtime) => _runtime = runtime;

    [HttpPost("versions")]
    public ActionResult<WorkflowVersion> PublishVersion([FromBody] WorkflowVersion version) =>
        Ok(_runtime.Mutate(plane => plane.Registry.PublishVersion(version)));

    [HttpPost("runs")]
    public ActionResult<WorkflowRun> StartRun([FromBody] StartRunRequest request) => Ok(
        _runtime.Mutate(plane =>
        {
            var run = plane.Registry.CreateRun(request.WorkflowRunId, request.WorkflowVersionId);
            plane.Registry.MoveRun(run.RunId, WorkflowRunState.Queued,
                Context("run queued"));
            return plane.Registry.MoveRun(run.RunId, WorkflowRunState.Running,
                Context("run started"));
        }));

    [HttpPost("runs/{runId}/stages")]
    public ActionResult<StageRun> AddStage(string runId, [FromBody] AddStageRequest request) => Ok(
        _runtime.Mutate(plane => plane.Registry.AddStage(
            runId, request.StageRunId, request.StageType, request.Iteration, request.Reason)));

    [HttpPost("stages/{stageRunId}/executions")]
    public ActionResult<Execution> AddExecution(string stageRunId, [FromBody] AddExecutionRequest request) =>
        Ok(_runtime.Mutate(plane => plane.Registry.AddExecution(stageRunId, request.ExecutionId)));

    [HttpPost("executions/{executionId}/assign")]
    public ActionResult<Assignment> Assign(string executionId, [FromBody] DispatchRequest request) => Ok(
        _runtime.Mutate(plane => plane.Dispatcher.Dispatch(
            executionId, request.WorkerId, request.AgentId, request.RequiredCapabilities,
            request.PolicyRevisionId, TimeSpan.FromSeconds(request.LeaseSeconds),
            request.HandoffId, request.TaskContext, request.ProviderId)));

    [HttpPost("executions/{executionId}/cancel")]
    public IActionResult Cancel(string executionId, [FromBody] CancelRequest request)
    {
        _runtime.Mutate(plane => plane.Dispatcher.DispatchCancel(executionId, request.Reason));
        return Accepted();
    }

    [HttpPost("handoffs")]
    public ActionResult<HandoffContext> IssueHandoff([FromBody] IssueHandoffRequest request) => Ok(
        _runtime.Mutate(plane => plane.IssueHandoff(
            request.SourceStageRunId, request.ExecutionId, request.TargetStageType,
            request.RequiredCapabilities, request.Workspace, request.TaskContext)));

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
        audit = plane.Registry.Audit.Records.ToArray(),
    }));

    private static TransitionContext Context(string reason) =>
        new("durable-api", reason, SchemaVersions.Registry);
}

public sealed record StartRunRequest(string WorkflowRunId, string WorkflowVersionId);
public sealed record AddStageRequest(string StageRunId, StageType StageType, int Iteration, string? Reason);
public sealed record AddExecutionRequest(string ExecutionId);
public sealed record DispatchRequest(
    string WorkerId,
    string AgentId,
    IReadOnlyList<string> RequiredCapabilities,
    string PolicyRevisionId,
    int LeaseSeconds,
    string? HandoffId = null,
    string TaskContext = "{}",
    string? ProviderId = null);
public sealed record CancelRequest(string Reason);
public sealed record RequestApprovalRequest(
    string StageRunId,
    string AssignmentId,
    PolicyDecisionRequest Decision,
    int WindowSeconds);
public sealed record DecideApprovalRequest(bool Granted, string Actor, string Reason);
public sealed record IssueHandoffRequest(
    string SourceStageRunId,
    string ExecutionId,
    StageType TargetStageType,
    IReadOnlyList<string> RequiredCapabilities,
    WorkspaceReference Workspace,
    string TaskContext = "{}");
