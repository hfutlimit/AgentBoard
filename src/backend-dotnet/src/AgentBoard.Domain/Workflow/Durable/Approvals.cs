// SPDX-License-Identifier: MIT
using AgentBoard.Contracts;
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Workflow.Durable;

/// <summary>One append-only audit record (doc 150 NFR-011, PR-015).</summary>
/// <remarks>
/// No field here can carry a secret by construction: the actor is an identity,
/// the subject is an id, and the reason is operator-authored text that redaction
/// has already cleared. Audit records reference transitions and decisions; they
/// never embed prompts or provider output (doc 151 §10 rule 6).
/// </remarks>
public sealed record AuditRecord(
    long Sequence,
    string Actor,
    string Action,
    string SubjectId,
    string Reason,
    string? CorrelationId,
    DateTimeOffset At);

/// <summary>
/// The audit trail: append-only, gap-less sequence, replayable.
/// </summary>
public sealed partial class AuditTrail
{
    private readonly List<AuditRecord> _records = new();
    private readonly Func<DateTimeOffset> _clock;

    public AuditTrail(Func<DateTimeOffset> clock) => _clock = clock;

    public IReadOnlyList<AuditRecord> Records => _records;

    public AuditRecord Append(
        string actor,
        string action,
        string subjectId,
        string reason,
        string? correlationId = null)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(actor);
        ArgumentException.ThrowIfNullOrWhiteSpace(action);

        var record = new AuditRecord(_records.Count + 1, actor, action, subjectId, reason, correlationId, _clock());
        _records.Add(record);
        return record;
    }

    /// <summary>Records an accepted state transition with its full context.</summary>
    public AuditRecord RecordTransition<TState>(
        string entityKind,
        string entityId,
        StateTransition<TState> transition)
        where TState : struct, Enum =>
        Append(
            transition.Context.Actor,
            $"{entityKind}.transition",
            entityId,
            $"#{transition.Sequence} {transition.From} -> {transition.To}: {transition.Context.Reason} " +
            $"(schema {transition.Context.SchemaVersion})",
            transition.Context.CausationId);
}

public enum ApprovalState
{
    Pending,
    Granted,
    Denied,
    Expired,
}

/// <summary>
/// An open question to an Operator: this action needs approval before it may
/// run (doc 150 PR-005 REQUIRE_APPROVAL).
/// </summary>
public sealed record ApprovalRequest(
    string ApprovalId,
    string StageRunId,
    string AssignmentId,
    string PolicyRevisionId,
    string ActionKind,
    DateTimeOffset OpenedAt,
    DateTimeOffset ExpiresAt,
    ApprovalState State,
    string? DecidedBy,
    string? Reason);

/// <summary>
/// Approval inbox with bounded waits. An approval that nobody decides becomes
/// <see cref="ApprovalState.Expired"/> rather than holding a stage forever —
/// doc 151 §5.3 forbids indefinite waiting even when an approval channel
/// nominally exists.
/// </summary>
public sealed partial class ApprovalInbox
{
    private readonly Dictionary<string, ApprovalRequest> _requests = new(StringComparer.Ordinal);
    private readonly AuditTrail _audit;
    private readonly Func<DateTimeOffset> _clock;

    public ApprovalInbox(AuditTrail audit, Func<DateTimeOffset> clock)
    {
        _audit = audit;
        _clock = clock;
    }

    public IReadOnlyCollection<ApprovalRequest> Requests => _requests.Values;

    public ApprovalRequest Open(
        string approvalId,
        string stageRunId,
        string assignmentId,
        string policyRevisionId,
        string actionKind,
        TimeSpan approvalWindow)
    {
        var now = _clock();
        var request = new ApprovalRequest(
            approvalId, stageRunId, assignmentId, policyRevisionId, actionKind,
            now, now + approvalWindow, ApprovalState.Pending, null, null);

        if (!_requests.TryAdd(approvalId, request))
        {
            throw new DuplicateException($"approval '{approvalId}' already exists");
        }

        _audit.Append("server", "approval.opened", approvalId,
            $"action '{actionKind}' on stage '{stageRunId}' requires approval");
        return request;
    }

    public ApprovalRequest Decide(string approvalId, bool granted, string actor, string reason)
    {
        var request = Require(approvalId);

        if (request.State != ApprovalState.Pending)
        {
            throw new InvalidValueException($"approval '{approvalId}' is already {request.State}");
        }

        if (string.IsNullOrWhiteSpace(actor))
        {
            throw new InvalidValueException("an operator decision must name its actor (doc 150 PR-015)");
        }

        var decided = request with
        {
            State = granted ? ApprovalState.Granted : ApprovalState.Denied,
            DecidedBy = actor,
            Reason = reason,
        };

        _requests[approvalId] = decided;
        _audit.Append(actor, granted ? "approval.granted" : "approval.denied", approvalId, reason);
        return decided;
    }

    /// <summary>Sweeps pending approvals whose window elapsed. Returns how many expired.</summary>
    public int ExpireStale()
    {
        var now = _clock();
        var stale = _requests.Values
            .Where(r => r.State == ApprovalState.Pending && now >= r.ExpiresAt)
            .ToList();

        foreach (var request in stale)
        {
            _requests[request.ApprovalId] = request with { State = ApprovalState.Expired, Reason = "approval window elapsed" };
            _audit.Append("server", "approval.expired", request.ApprovalId, "operator did not decide within the window");
        }

        return stale.Count;
    }

    public ApprovalRequest Require(string approvalId) =>
        _requests.TryGetValue(approvalId, out var request)
            ? request
            : throw new NotFoundException($"approval '{approvalId}' not found");

    public bool IsGranted(string approvalId) =>
        _requests.TryGetValue(approvalId, out var request) && request.State == ApprovalState.Granted;
}
