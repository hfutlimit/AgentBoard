using AgentBoard.Node.Agents;
using Microsoft.Extensions.Options;

namespace AgentBoard.Node.Execution;

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

    /// <summary>
    /// Optional fallback when a <see cref="WorkflowMessage"/> arrives without
    /// <c>agent_type</c>. Default <c>null</c> (PR-3): missing agent_type is
    /// a publisher bug → throw to DLQ rather than silently routing to
    /// "workbuddy" (which masked PR-2 review P0-4).
    ///
    /// Operators can still set a non-null default for dev/integration where
    /// the publisher isn't yet fully wired (PR-5 will set agent_type at
    /// publish time from the task_type_routing table). Each fallback use
    /// is logged at WARN so missing config is visible.
    /// </summary>
    private readonly string? _defaultAgent;

    public WorkflowMessageMapper(IAgentAdapterRegistry registry, string? defaultAgent = null)
    {
        _registry = registry;
        _defaultAgent = string.IsNullOrWhiteSpace(defaultAgent) ? null : defaultAgent;
    }

    public ExecutionRequest MapToExecution(WorkflowMessage msg, string source)
    {
        // PR-3: 缺 agent_type → 优先用配置 default；都没有则抛 DLQ。
        // 之前版本是隐式 default="workbuddy" 把所有 task / review 路由到
        // WorkBuddy（错），是 P0-4 的根因。
        string? agentType = null;
        if (!string.IsNullOrWhiteSpace(msg.AgentType))
        {
            agentType = msg.AgentType;
        }
        else if (_defaultAgent is not null)
        {
            // 仅用于 dev / integration 兜底；PR-5 完成后这条分支应不触发。
            // 实际场景下应该 WARN 一行（这里不能直接 log；让 caller 决定）
            agentType = _defaultAgent;
        }
        if (string.IsNullOrWhiteSpace(agentType))
        {
            throw new InvalidDataException(
                $"workflow message missing 'agent_type' (event={msg.Event} " +
                $"entity={msg.EntityType}:{msg.EntityId}); " +
                "publisher must set agent_type — see PR-5 task_type_routing");
        }
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
            PayloadJson: msg.ToJson(),
            // P0-2：task type 透传，prompt 按 design/dev/qa 分执行语义
            TaskType: msg.TaskType);
    }

    private static (string WorkloadType, string KeySuffix) Classify(string eventName) => eventName switch
    {
        "task.available"          => (WorkloadTypes.Task, "task.available"),
        "task.assigned"           => (WorkloadTypes.Task, "task.assigned"),
        // PR-4: task.ready_for_review 故意不在这里 —— 它是 pre-assignment 事件
        // （任务进入 in_review，但还没选 reviewer），由 FastAPI 端 Python
        // workflow_worker 独占 internal_queue 选 reviewer，选完后再
        // 发 task.review_requested 到 agent 定向队列，.NET 才接管。
        // 如果 .NET 误收到 task.ready_for_review（broadcast queue 残留），
        // 会进 InvalidDataException → DLQ，运维能立刻看到 routing 错配。
        "task.review_requested"   => (WorkloadTypes.Review, "task.review"),
        "task.rejected"           => (WorkloadTypes.Rework, "task.rework"),
        "task.review_rejected"    => (WorkloadTypes.Rework, "task.rework"),
        "proposal.ticket_requested" => (WorkloadTypes.Ticket, "proposal.ticket"),
        "proposal.ticket_created"   => (WorkloadTypes.Ticket, "proposal.ticket"),
        _ => throw new InvalidDataException(
            $"workflow event '{eventName}' is not actionable by the .NET worker; drop to DLQ"),
    };
}
