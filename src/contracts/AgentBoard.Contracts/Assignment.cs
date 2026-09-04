// SPDX-License-Identifier: MIT
namespace AgentBoard.Contracts;

/// <summary>
/// The lease-backed grant that lets one Node run one attempt (doc 151 §5.4).
/// </summary>
/// <param name="LeaseEpoch">
/// Monotonic fencing token. Reassignment always produces a
/// <em>new</em> Assignment with a higher epoch rather than mutating this one,
/// which is why the record is positional and therefore immutable.
/// </param>
/// <remarks>
/// <para>
/// doc 151 §5.4: "Server 只接受当前 lease epoch 的有效状态更新。Node 必须在本地
/// 记录 assignment 和 epoch；无法续租或发现 epoch 过期时停止提交业务结果。"
/// </para>
/// <para>
/// The pair (<see cref="ExecutionId"/>, <see cref="AttemptId"/>) is what makes
/// an assignment specific to one physical try: a retry of the same execution
/// gets a new attempt and therefore a new assignment, so a late result from the
/// previous attempt can be recognised as stale rather than accepted.
/// </para>
/// </remarks>
public sealed record Assignment(
    string AssignmentId,
    string WorkflowRunId,
    string StageRunId,
    string ExecutionId,
    string AttemptId,
    string WorkerId,
    string AgentId,
    string LeaseId,
    long LeaseEpoch,
    IReadOnlyList<string> RequiredCapabilities,
    DateTimeOffset IssuedAt,
    DateTimeOffset ExpiresAt,
    string PolicyRevisionId)
{
    /// <summary>True once the lease window has elapsed.</summary>
    public bool IsExpired(DateTimeOffset now) => now >= ExpiresAt;
}
