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
    private readonly List<string> _order = new();
    private readonly Func<DateTimeOffset> _clock;

    public TaskStatusProjectionOutbox(Func<DateTimeOffset> clock) => _clock = clock;

    public IReadOnlyCollection<TaskStatusProjection> Entries => Capture();

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
        _order.Add(projectionId);
        return entry;
    }

    /// <summary>
    /// Claims the first unfinished entry for each task in durable insertion
    /// order. A retry delay or live claim must not let a later status overtake
    /// it; other tasks remain independently deliverable.
    /// </summary>
    public TaskStatusProjection? BeginNext(TimeSpan claimWindow)
    {
        var now = _clock();
        var next = _order.Select(id => _entries[id])
            .Where(entry => entry.State != TaskStatusProjectionState.Completed)
            .GroupBy(entry => entry.TaskId)
            .Select(entries => entries.First())
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

    internal void Clear()
    {
        _entries.Clear();
        _order.Clear();
    }

    internal void Restore(IReadOnlyList<TaskStatusProjection> entries)
    {
        Clear();
        foreach (var entry in entries)
        {
            _entries.Add(entry.ProjectionId, entry);
            _order.Add(entry.ProjectionId);
        }
    }

    public IReadOnlyList<TaskStatusProjection> Capture() => _order.Select(id => _entries[id]).ToList();
}
