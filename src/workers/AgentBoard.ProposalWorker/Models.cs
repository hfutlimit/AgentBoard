using System.Text.Json;

namespace AgentBoard.ProposalWorker;

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
/// What the worker actually executes. Workload-agnostic; ProposalMapper is
/// the only place that knows about Proposal-specific message format.
/// </summary>
public sealed record ExecutionRequest(
    string ExecutionKey,
    string WorkloadType,
    long WorkloadId,
    string AgentType,
    int Round,
    string Source,
    string PayloadJson);

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
    string? Prompt);

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
