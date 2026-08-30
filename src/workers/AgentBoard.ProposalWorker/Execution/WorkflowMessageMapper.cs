using AgentBoard.ProposalWorker.Agents;
using Microsoft.Extensions.Options;

namespace AgentBoard.ProposalWorker.Execution;

/// <summary>
/// Sprint 12 (Generic AgentWorker). Maps a <see cref="WorkflowMessage"/>
/// from the <c>agentboard.workflow</c> RabbitMQ namespace into the
/// workload-agnostic <see cref="ExecutionRequest"/>. The mapper is
/// the single source of truth for "which workflow event becomes which
/// <see cref="WorkloadTypes"/>". Adding a new actionable event here
/// is enough; the dispatcher reads the <c>WorkloadType</c> string
/// from the inbox row to route to the right adapter.
///
/// Routing rules (2026-08-30 review follow-up — close the orchestration
/// gap so ProposalWorker can stop being proposal-only):
/// <list type="bullet">
///   <item><c>task.available</c> / <c>task.assigned</c> → developer claims
///         a Task and runs the implementation CLI.</item>
///   <item><c>task.ready_for_review</c> / <c>task.review_requested</c>
///         → reviewer runs the review CLI.</item>
///   <item><c>task.rejected</c> / <c>task.review_rejected</c>
///         → original developer re-opens the Task and runs the rework
///         CLI; <c>ref_id</c> carries the review round.</item>
///   <item><c>proposal.ticket_requested</c> / <c>proposal.ticket_created</c>
///         → planner materializes a converged proposal into Story +
///         Task DAG. This is the path the review's
///         <c>auto_create_ticket</c> should reuse; today it lands in
///         the inbox and the planner adapter (a future entry) takes
///         over. We register the inbox row regardless so the path is
///         exercised end-to-end.</item>
/// </list>
///
/// Events NOT in the routing table (e.g. <c>story.created</c>,
/// <c>comment.replied</c>, <c>review.vote_cast</c>) are intentionally
/// dropped with <see cref="InvalidDataException"/> so the broker
/// routes them to the DLQ. The FastAPI <c>workflow_worker</c> is
/// the right place to log-and-ack them; the .NET worker has nothing
/// to execute for those.
/// </summary>
public sealed class WorkflowMessageMapper
{
    private readonly IAgentAdapterRegistry _registry;

    public WorkflowMessageMapper(IAgentAdapterRegistry registry) => _registry = registry;

    public ExecutionRequest MapToExecution(WorkflowMessage msg, string source, string defaultAgent = "workbuddy")
    {
        // Backward compat: workflow messages without agent_type fall back to
        // the worker default. Production deployment overrides per-project
        // routing via the project's "default agent" setting; the
        // proposal.clarify path already does the same fall-back.
        var agentType = string.IsNullOrWhiteSpace(msg.AgentType) ? defaultAgent : msg.AgentType;
        if (!_registry.IsRegistered(agentType))
            throw new InvalidAgentException(agentType);

        var (workloadType, keySuffix) = Classify(msg.Event);
        // Execution-key shape is intentionally stable per (event, entity, ref,
        // agent) so redeliveries are idempotent. The ref_id segment matters
        // for rework: same task re-issued for round=2 must produce a fresh
        // execution (otherwise rework stalls on the dedupe row from round 1).
        var refSegment = msg.RefId?.ToString() ?? "0";
        return new ExecutionRequest(
            ExecutionKey: $"workflow:{keySuffix}:{msg.EntityId}:{refSegment}:{agentType}",
            WorkloadType: workloadType,
            WorkloadId: msg.EntityId,
            AgentType: agentType,
            Round: msg.RefId.HasValue ? (int)msg.RefId.Value : 0,
            Source: source,
            PayloadJson: msg.ToJson());
    }

    private static (string WorkloadType, string KeySuffix) Classify(string eventName) => eventName switch
    {
        "task.available"          => (WorkloadTypes.Task, "task.available"),
        "task.assigned"           => (WorkloadTypes.Task, "task.assigned"),
        "task.ready_for_review"   => (WorkloadTypes.Review, "task.review"),
        "task.review_requested"   => (WorkloadTypes.Review, "task.review"),
        "task.rejected"           => (WorkloadTypes.Rework, "task.rework"),
        "task.review_rejected"    => (WorkloadTypes.Rework, "task.rework"),
        "proposal.ticket_requested" => (WorkloadTypes.Ticket, "proposal.ticket"),
        "proposal.ticket_created"   => (WorkloadTypes.Ticket, "proposal.ticket"),
        _ => throw new InvalidDataException(
            $"workflow event '{eventName}' is not actionable by the .NET worker; drop to DLQ"),
    };
}
