// SPDX-License-Identifier: MIT
using AgentBoard.Contracts;
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Workflow.Durable;

/// <summary>
/// Whether a message/result may act on the lease it names (doc 151 §5.4,
/// doc 150 PR-008).
/// </summary>
public enum LeaseVerdict
{
    /// <summary>Current epoch, not expired: the update is acceptable.</summary>
    Valid,

    /// <summary>Names no assignment the Server ever granted.</summary>
    Unknown,

    /// <summary>
    /// Names a real assignment whose epoch is behind the execution's current
    /// epoch — the doc 151 §4.2 invariant 5 case ("旧 attempt 的迟到 result
    /// 不能覆盖新 lease epoch 的结果").
    /// </summary>
    StaleEpoch,

    /// <summary>Current epoch, but the lease window elapsed. Recovery means a new epoch.</summary>
    Expired,
}

/// <summary>
/// Server-side assignment/lease bookkeeping with monotonic fencing tokens.
/// </summary>
/// <remarks>
/// <para>
/// Epochs are per execution and strictly increasing: a reassignment never
/// mutates an existing <see cref="Assignment"/> (the contract record is
/// immutable), it grants a new one at a higher epoch. Older assignments then
/// fail <see cref="Check"/> with <see cref="LeaseVerdict.StaleEpoch"/>, which
/// is what lets the Server reject stale results mechanically rather than by
/// trusting node honesty.
/// </para>
/// <para>
/// Time comes from an injected clock: lease expiry is decided by the Server's
/// view of time, and tests must be able to move the clock past a deadline
/// without sleeping.
/// </para>
/// </remarks>
public sealed partial class LeaseRegistry
{
    private readonly Dictionary<string, Assignment> _byId = new(StringComparer.Ordinal);
    private readonly Dictionary<string, long> _currentEpoch = new(StringComparer.Ordinal);
    private readonly Func<DateTimeOffset> _clock;

    public LeaseRegistry(Func<DateTimeOffset> clock)
    {
        _clock = clock ?? throw new ArgumentNullException(nameof(clock));
    }

    public IReadOnlyCollection<Assignment> Assignments => _byId.Values;

    /// <summary>Epoch the next assignment for this execution must carry.</summary>
    public long NextEpoch(string executionId) =>
        (_currentEpoch.TryGetValue(executionId, out var epoch) ? epoch : 0) + 1;

    public Assignment? CurrentFor(string executionId)
    {
        if (!_currentEpoch.TryGetValue(executionId, out var epoch))
        {
            return null;
        }

        return _byId.Values.FirstOrDefault(a =>
            string.Equals(a.ExecutionId, executionId, StringComparison.Ordinal) && a.LeaseEpoch == epoch);
    }

    /// <summary>
    /// Records a granted assignment. The epoch must be exactly
    /// <see cref="NextEpoch"/> for the execution, so callers cannot skip
    /// numbers or reuse one — a reassignment always supersedes the previous
    /// lease by construction (doc 150 PR-008: "重分配必须生成新的
    /// assignment/lease epoch").
    /// </summary>
    public Assignment Grant(Assignment assignment)
    {
        var errors = AssignmentValidator.Validate(assignment);
        if (errors.Count > 0)
        {
            throw new InvalidValueException(
                $"invalid assignment: {string.Join("; ", errors.Select(e => $"{e.Field} {e.Reason}"))}");
        }

        if (_byId.ContainsKey(assignment.AssignmentId))
        {
            throw new DuplicateException($"assignment '{assignment.AssignmentId}' already exists");
        }

        if (assignment.LeaseEpoch != NextEpoch(assignment.ExecutionId))
        {
            throw new InvalidValueException(
                $"assignment epoch {assignment.LeaseEpoch} is not the next epoch " +
                $"{NextEpoch(assignment.ExecutionId)} for execution '{assignment.ExecutionId}'");
        }

        _byId[assignment.AssignmentId] = assignment;
        _currentEpoch[assignment.ExecutionId] = assignment.LeaseEpoch;
        return assignment;
    }

    /// <summary>
    /// Extends a live lease without changing its epoch. An expired lease can
    /// never be renewed — that would let a long-dead node resurrect its right
    /// to produce outcomes — so callers must reassign at a new epoch instead
    /// (doc 150 PR-008).
    /// </summary>
    public Assignment Renew(string assignmentId, DateTimeOffset newExpiresAt)
    {
        var assignment = Require(assignmentId);
        var now = _clock();

        if (assignment.IsExpired(now))
        {
            throw new InvalidValueException(
                $"lease '{assignmentId}' expired at {assignment.ExpiresAt:O}; renew by reassigning at a new epoch");
        }

        var verdict = Check(assignmentId, assignment.LeaseEpoch);
        if (verdict != LeaseVerdict.Valid)
        {
            throw new InvalidValueException($"cannot renew a lease whose verdict is {verdict}");
        }

        if (newExpiresAt <= now)
        {
            throw new InvalidValueException("a renewal must extend into the future");
        }

        var renewed = assignment with { ExpiresAt = newExpiresAt };
        _byId[assignmentId] = renewed;
        return renewed;
    }

    /// <summary>
    /// Decides whether an update claiming <paramref name="assignmentId"/> at
    /// <paramref name="leaseEpoch"/> may be accepted.
    /// </summary>
    public LeaseVerdict Check(string assignmentId, long leaseEpoch)
    {
        if (!_byId.TryGetValue(assignmentId, out var assignment))
        {
            return LeaseVerdict.Unknown;
        }

        var current = _currentEpoch.TryGetValue(assignment.ExecutionId, out var epoch) ? epoch : -1;

        // Stale in either direction: the assignment itself is a superseded
        // lease, or the caller claims an epoch other than the one this
        // assignment was granted at (a forged epoch fences just as hard).
        if (assignment.LeaseEpoch < current || leaseEpoch != assignment.LeaseEpoch)
        {
            return LeaseVerdict.StaleEpoch;
        }

        if (assignment.IsExpired(_clock()))
        {
            return LeaseVerdict.Expired;
        }

        return LeaseVerdict.Valid;
    }

    /// <summary>
    /// True when any lease for the execution is currently acceptable — used to
    /// expire idle assignments during recovery scans.
    /// </summary>
    public IReadOnlyList<Assignment> ExpiredAssignments(DateTimeOffset now) =>
        _byId.Values.Where(a => a.IsExpired(now)).ToList();

    public Assignment Require(string assignmentId) =>
        _byId.TryGetValue(assignmentId, out var assignment)
            ? assignment
            : throw new NotFoundException($"assignment '{assignmentId}' not found");
}
