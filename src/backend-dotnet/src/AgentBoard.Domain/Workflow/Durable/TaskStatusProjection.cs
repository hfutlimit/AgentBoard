// SPDX-License-Identifier: MIT

namespace AgentBoard.Domain.Workflow.Durable;

public enum TaskStatusProjectionState
{
    Pending,
    Dispatching,
    Completed,
}

/// <summary>
/// Durable intent to project a workflow transition onto the FastAPI-owned Task.
/// It commits with workflow state; delivery is asynchronous and restart-safe.
/// </summary>
public sealed record TaskStatusProjection(
    string ProjectionId,
    string RunId,
    int TaskId,
    string TargetStatus,
    string? StatusReason,
    string Reason,
    TaskStatusProjectionState State,
    int Attempts,
    DateTimeOffset AvailableAt,
    DateTimeOffset? ClaimExpiresAt = null,
    string? LastError = null);

public sealed class TaskStatusProjectionOutbox
{
    private readonly Dictionary<string, TaskStatusProjection> _entries = new(StringComparer.Ordinal);
    private readonly Func<DateTimeOffset> _clock;

    public TaskStatusProjectionOutbox(Func<DateTimeOffset> clock) => _clock = clock;

    public IReadOnlyCollection<TaskStatusProjection> Entries => _entries.Values;

    public TaskStatusProjection Enqueue(
        string projectionId,
        string runId,
        int taskId,
        string targetStatus,
        string? statusReason,
        string reason)
    {
        if (taskId <= 0) throw new Common.InvalidValueException("task projection requires a positive task id");
        ArgumentException.ThrowIfNullOrWhiteSpace(projectionId);
        ArgumentException.ThrowIfNullOrWhiteSpace(runId);
        ArgumentException.ThrowIfNullOrWhiteSpace(targetStatus);
        ArgumentException.ThrowIfNullOrWhiteSpace(reason);

        var entry = new TaskStatusProjection(
            projectionId, runId, taskId, targetStatus, statusReason, reason,
            TaskStatusProjectionState.Pending, 0, _clock());
        if (!_entries.TryAdd(projectionId, entry))
        {
            throw new Common.DuplicateException($"task projection '{projectionId}' already exists");
        }
        return entry;
    }

    /// <summary>
    /// Claims only the oldest due entry. Serial delivery preserves state-machine
    /// order and an expired claim recovers a crash after the remote HTTP call.
    /// </summary>
    public TaskStatusProjection? BeginNext(TimeSpan claimWindow)
    {
        var now = _clock();
        var next = _entries.Values
            .Where(entry =>
                (entry.State == TaskStatusProjectionState.Pending && entry.AvailableAt <= now)
                || (entry.State == TaskStatusProjectionState.Dispatching && entry.ClaimExpiresAt <= now))
            .OrderBy(entry => entry.AvailableAt)
            .ThenBy(entry => entry.ProjectionId, StringComparer.Ordinal)
            .FirstOrDefault();
        if (next is null) return null;

        var claimed = next with
        {
            State = TaskStatusProjectionState.Dispatching,
            Attempts = next.Attempts + 1,
            ClaimExpiresAt = now.Add(claimWindow),
        };
        _entries[claimed.ProjectionId] = claimed;
        return claimed;
    }

    public TaskStatusProjection Complete(string projectionId)
    {
        var current = Require(projectionId);
        var completed = current with
        {
            State = TaskStatusProjectionState.Completed,
            ClaimExpiresAt = null,
            LastError = null,
        };
        _entries[projectionId] = completed;
        return completed;
    }

    public TaskStatusProjection Retry(string projectionId, string error, TimeSpan delay)
    {
        var current = Require(projectionId);
        var pending = current with
        {
            State = TaskStatusProjectionState.Pending,
            AvailableAt = _clock().Add(delay),
            ClaimExpiresAt = null,
            LastError = string.IsNullOrWhiteSpace(error) ? "projection failed" : error,
        };
        _entries[projectionId] = pending;
        return pending;
    }

    public TaskStatusProjection Require(string projectionId) =>
        _entries.TryGetValue(projectionId, out var entry)
            ? entry
            : throw new Common.NotFoundException($"task projection '{projectionId}' not found");

    internal void Clear() => _entries.Clear();

    internal void Restore(IReadOnlyList<TaskStatusProjection> entries)
    {
        foreach (var entry in entries) _entries[entry.ProjectionId] = entry;
    }

    public IReadOnlyList<TaskStatusProjection> Capture() => _entries.Values.ToList();
}
