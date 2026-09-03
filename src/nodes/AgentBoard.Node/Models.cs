using System.Text.Json;

namespace AgentBoard.Node;

// =============================================================================
// Sprint 1: Execution State Machine
// =============================================================================

/// <summary>
/// Execution lifecycle. State transitions are managed exclusively by
/// <see cref="Execution.ExecutionCoordinator"/>; store writes are CAS-guarded
/// so concurrent writers cannot race a single execution across two terminals.
/// </summary>
public enum ExecutionState
{
    Pending = 0,
    Claimed = 1,
    Starting = 2,
    Running = 3,
    Succeeded = 4,
    Failed = 5,
    TimedOut = 6,
    Cancelled = 7,
    /// <summary>
    /// The agent's business result is known (Succeeded / Failed / etc.)
    /// but the terminal write to the executions table could not be
    /// persisted after retry. The dispatcher still marks the inbox
    /// `completed` so the work is not redone; an operator must
    /// reconcile the row manually. Surfaces as a clear "degraded" in
    /// <c>WorkerState.Snapshot</c> so dashboards and the install
    /// /health check can alert.
    /// </summary>
    Degraded = 8,
}

// =============================================================================
// Sprint 4: Generic execution types
// =============================================================================

/// <summary>
/// What arrives off RabbitMQ. Still Proposal-shaped on the wire for backward
/// compat, but the worker treats it as an opaque message and uses the
/// <see cref="Execution.ProposalMessageMapper"/> to translate.
/// </summary>
public sealed record ProposalMessage(long ProposalId, int Round, string Reason, string Timestamp, string? AgentType = null)
{
    public static ProposalMessage Parse(ReadOnlyMemory<byte> body)
    {
        using var doc = JsonDocument.Parse(body);
        var root = doc.RootElement;
        if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty("proposal_id", out var id) ||
            !id.TryGetInt64(out var proposalId) || proposalId <= 0)
            throw new InvalidDataException("proposal message requires positive proposal_id");
        var round = root.TryGetProperty("round", out var r) && r.TryGetInt32(out var value) ? Math.Max(0, value) : 0;
        var reason = root.TryGetProperty("reason", out var why) ? why.GetString() ?? "" : "";
        var timestamp = root.TryGetProperty("ts", out var ts) ? ts.GetString() ?? "" : "";
        var agentType = root.TryGetProperty("agent_type", out var at) ? at.GetString() : null;
        return new ProposalMessage(proposalId, round, reason, timestamp,
            string.IsNullOrWhiteSpace(agentType) ? null : agentType);
    }

    public string ToJson() => JsonSerializer.Serialize(new
    {
        proposal_id = ProposalId,
        round = Round,
        reason = Reason,
        ts = Timestamp,
        agent_type = AgentType
    });
}

/// <summary>
/// What the worker actually executes. Workload-agnostic; the per-kind
/// mappers (ProposalMessageMapper, WorkflowMessageMapper) are the only
/// places that know about the specific RabbitMQ message formats.
/// </summary>
public sealed record ExecutionRequest(
    string ExecutionKey,
    string WorkloadType,
    long WorkloadId,
    string AgentType,
    int Round,
    string Source,
    string PayloadJson,
    // P0-2（2026-09-01 review）：task.assigned 消息里的 task.type（design/dev/qa/bug）。
    // 可空：legacy 消息没有该字段 → prompt 退回 implementation 语义。
    string? TaskType = null);

/// <summary>
/// Canonical workload-type taxonomy shared between the mappers and the
/// dispatcher. Adding a new value here is a deliberate change — the
/// dispatcher's adapter-routing table is keyed on this string.
/// </summary>
public static class WorkloadTypes
{
    /// <summary>Proposal clarify/ticket/story (legacy proposal queue).</summary>
    public const string Proposal = "proposal";
    /// <summary>Developer runs a Task (task.available / task.assigned).</summary>
    public const string Task = "task";
    /// <summary>Reviewer runs a Task review (task.review_requested / task.ready_for_review).</summary>
    public const string Review = "review";
    /// <summary>Developer fixes a Task after a reject (task.rejected / task.review_rejected).</summary>
    public const string Rework = "rework";
    /// <summary>Planner materializes a proposal into Story + Task DAG (proposal.ticket_requested / proposal.ticket_created).</summary>
    public const string Ticket = "ticket";
}

/// <summary>
/// Sprint 12 (Generic AgentWorker). A workflow event from the
/// <c>agentboard.workflow</c> namespace. Mirrors the FastAPI
/// <c>WorkflowMessage</c> contract: only carries locator info
/// (event + entity_id + optional ref_id); state is always re-read
/// from the AgentBoard REST API by the executing agent.
///
/// PR-2 shape alignment with FastAPI:
/// - <c>agent_type</c> was already present; defaulting to null is
///   backward-compatible.
/// - <c>workload_type</c> tells the dispatcher which adapter family
///   to use (task / review / rework / ticket). PR-3 will use this
///   instead of inferring from event name.
/// - <c>correlation_id</c> threads through the whole chain
///   (proposal → story → tasks → review) so logs and the state
///   machine can trace "which chain broke where".
///
/// PR-11: <c>agent_id</c> is the logical agent identity (e.g.
/// <c>codex-dev-1</c> vs <c>codex-dev-2</c>). Combined with
/// <c>agent_type</c> it lets consumers distinguish multiple agents
/// of the same CLI family for MCP API key, audit trail, and
/// agent-specific model. Routing key still uses <c>worker_id</c>
/// (PR-5); <c>agent_id</c> is body-only.
///
/// P0-2（2026-09-01 review）： <c>task_type</c> carries the Task's
/// type (design / dev / qa / bug) on <c>task.assigned</c> so the
/// prompt builder can specialize execution semantics per type.
/// Optional and body-only; legacy messages without it fall back
/// to the implementation prompt.
/// </summary>
public sealed record WorkflowMessage(
    string Event,
    string EntityType,
    long EntityId,
    long? RefId,
    string Timestamp,
    string? AgentType = null,
    string? WorkloadType = null,
    string CorrelationId = "",
    string? AgentId = null,
    string? TaskType = null)
{
    public static WorkflowMessage Parse(ReadOnlyMemory<byte> body)
    {
        using var doc = JsonDocument.Parse(body);
        var root = doc.RootElement;
        if (root.ValueKind != JsonValueKind.Object)
            throw new InvalidDataException("workflow message must be a JSON object");
        var ev = root.TryGetProperty("event", out var e) ? e.GetString() ?? "" : "";
        if (string.IsNullOrWhiteSpace(ev))
            throw new InvalidDataException("workflow message requires non-empty 'event' field");
        var et = root.TryGetProperty("entity_type", out var etp) ? etp.GetString() ?? "" : "";
        if (string.IsNullOrWhiteSpace(et))
            throw new InvalidDataException("workflow message requires 'entity_type' field");
        if (!root.TryGetProperty("entity_id", out var idp) || !idp.TryGetInt64(out var entityId) || entityId <= 0)
            throw new InvalidDataException("workflow message requires positive entity_id");
        long? refId = null;
        if (root.TryGetProperty("ref_id", out var rp) &&
            (rp.ValueKind == JsonValueKind.Number && rp.TryGetInt64(out var rid) && rid > 0))
        {
            refId = rid;
        }
        var ts = root.TryGetProperty("ts", out var tsp) ? tsp.GetString() ?? "" : "";
        var agentType = root.TryGetProperty("agent_type", out var at) ? at.GetString() : null;
        var workloadType = root.TryGetProperty("workload_type", out var wt) ? wt.GetString() : null;
        var correlationId = root.TryGetProperty("correlation_id", out var ct) ? ct.GetString() ?? "" : "";
        // PR-11：logical agent_id（区分同 type 多 agent）
        var agentId = root.TryGetProperty("agent_id", out var aid) ? aid.GetString() : null;
        // P0-2：task type（design/dev/qa/bug），仅 task.assigned 携带
        var taskType = root.TryGetProperty("task_type", out var tt) ? tt.GetString() : null;
        return new WorkflowMessage(
            ev, et, entityId, refId, ts,
            string.IsNullOrWhiteSpace(agentType) ? null : agentType,
            string.IsNullOrWhiteSpace(workloadType) ? null : workloadType,
            correlationId,
            string.IsNullOrWhiteSpace(agentId) ? null : agentId,
            string.IsNullOrWhiteSpace(taskType) ? null : taskType);
    }

    public string ToJson() => JsonSerializer.Serialize(new
    {
        @event = Event,
        entity_type = EntityType,
        entity_id = EntityId,
        ref_id = RefId,
        ts = Timestamp,
        agent_type = AgentType,
        workload_type = WorkloadType,
        correlation_id = CorrelationId,
        // PR-11：logical agent_id（区分同 type 多 agent）
        agent_id = AgentId,
        // P0-2：task type（design/dev/qa/bug）
        task_type = TaskType
    });
}

/// <summary>
/// Sprint 12. Discriminated union of every wire-format the worker accepts.
/// Lets the RabbitMQ consumer stay generic: peek at the JSON, classify,
/// then hand the typed payload to the matching mapper.
///
/// The discriminator rules are deliberately minimal — they only answer
/// "which parser?" not "is this actionable?"; mappers and the consumer's
/// switch do the actionable-vs-drop decision downstream.
/// </summary>
public abstract record WorkloadMessage
{
    public static WorkloadMessage Parse(ReadOnlyMemory<byte> body)
    {
        using var doc = JsonDocument.Parse(body);
        var root = doc.RootElement;
        if (root.ValueKind != JsonValueKind.Object)
            throw new InvalidDataException("workload message must be a JSON object");
        if (root.TryGetProperty("proposal_id", out _))
            return new Proposal(ProposalMessage.Parse(body));
        if (root.TryGetProperty("event", out _))
            return new Workflow(WorkflowMessage.Parse(body));
        throw new InvalidDataException(
            "workload message must contain either 'proposal_id' (legacy proposal) or 'event' (workflow)");
    }

    /// <summary>Legacy proposal-message payload (Sprint 1+).</summary>
    public sealed record Proposal(ProposalMessage Inner) : WorkloadMessage;
    /// <summary>Workflow event from <c>agentboard.workflow</c> (Sprint 12).</summary>
    public sealed record Workflow(WorkflowMessage Inner) : WorkloadMessage;
}

/// <summary>
/// Per-adapter execution context. Adapter is free to ignore any field but
/// the prompt builder uses PayloadJson to construct the agent input.
/// </summary>
public sealed record ExecutionContext(
    long ExecutionId,
    string ExecutionKey,
    string WorkloadType,
    long WorkloadId,
    int Round,
    string AgentType,
    string PayloadJson,
    string? Prompt,
    // P0-2（2026-09-01 review）：Task 的类型（design/dev/qa/bug）。
    // prompt builder 按 type 分执行语义；null → implementation 语义。
    string? TaskType = null);

public sealed record AgentExecutionResult(
    bool Success,
    string? OutputJson,
    string? ErrorMessage,
    int? ExitCode,
    TimeSpan Duration,
    bool TimedOut = false,
    bool Cancelled = false);

/// <summary>
/// One row of <c>executions</c> as the rest of the system sees it.
/// </summary>
public sealed record ExecutionRecord(
    long Id,
    long WorkloadId,
    string WorkloadType,
    int Round,
    string Reason,
    string Source,
    string AgentType,
    string Status,
    DateTimeOffset StartedAt,
    DateTimeOffset? FinishedAt,
    int? ExitCode,
    string Output,
    string? Error,
    string? FailureReason,
    string? ErrorStack,
    string Payload);

/// <summary>
/// One row of <c>worker_execution_inbox</c>. Sprint 2 durable inbox.
/// </summary>
public sealed record InboxRecord(
    long Id,
    string ExecutionKey,
    string WorkloadType,
    long WorkloadId,
    string AgentType,
    int Round,
    string PayloadJson,
    string Status,
    DateTimeOffset ReceivedAt,
    DateTimeOffset? DispatchedAt,
    DateTimeOffset? CompletedAt,
    int Attempt,
    string? ErrorMessage);

/// <summary>
/// Live execution known to WorkerState. Removed when the coordinator hits a
/// terminal state. Powers the per-agent counter in <c>agents.*.running</c>.
/// </summary>
public sealed record ActiveExecution(
    long ExecutionId,
    string ExecutionKey,
    string WorkloadType,
    long WorkloadId,
    string AgentType,
    DateTimeOffset StartedAt);
