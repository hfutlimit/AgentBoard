// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board.Dtos;
using AgentBoard.Application.Scheduling.Dtos;

namespace AgentBoard.Application.Board;

/// <summary>
/// Application-layer provider for the AgentSchedule / AgentRun
/// aggregates. Stage 2 module 4 ships the four write endpoints
/// (<c>PATCH</c> / <c>DELETE</c> schedule + <c>GET</c> / <c>POST</c>
/// runs); the same provider may grow additional methods in module 2
/// (Agents) — see the comment on <c>SchedulingProvider</c>
/// for the partial-class extension point.
///
/// All methods are async + cancellation-token aware to match the
/// rest of the <c>Board</c> namespace convention. Methods return
/// <c>null</c> (or <c>false</c>) on missing-aggregate so the
/// controller can map to 404 without a try/catch — see
/// <c>WebhookProvider</c> for the same pattern.
/// </summary>
public interface ISchedulingProvider : IProvider
{
    /// <summary>
    /// Apply a partial update to <paramref name="id"/>. Only non-null
    /// fields on <paramref name="body"/> are written. Returns the
    /// post-update DTO, or <c>null</c> when the schedule doesn't
    /// exist (controller maps to 404).
    /// </summary>
    Task<AgentScheduleDto?> UpdateScheduleAsync(
        int id,
        SchedulePatchRequest body,
        CancellationToken ct = default);

    /// <summary>
    /// Hard-delete the schedule row and its <c>agent_runs</c> children
    /// (cascade is the database's responsibility — the provider
    /// ensures both are removed within a single UoW commit).
    /// </summary>
    /// <returns><c>true</c> when a row was deleted, <c>false</c> when
    /// the schedule didn't exist (controller maps to 404).</returns>
    Task<bool> DeleteScheduleAsync(int id, CancellationToken ct = default);

    /// <summary>
    /// Page through the runs owned by <paramref name="scheduleId"/>.
    /// <paramref name="status"/> is an optional exact-match filter
    /// (one of <c>pending|running|success|failed|cancelled</c>).
    /// </summary>
    Task<IReadOnlyList<AgentRunDto>> ListRunsAsync(
        int scheduleId,
        string? status,
        int limit,
        int offset,
        CancellationToken ct = default);

    /// <summary>
    /// Kick off a manual run for the given schedule. The new row is
    /// created with <c>status='running'</c> and a generated
    /// <c>idempotency_key</c> when the caller didn't supply one.
    /// Returns the freshly-created run DTO, or <c>null</c> when the
    /// schedule is missing (controller maps to 404).
    /// </summary>
    Task<AgentRunDto?> CreateRunAsync(
        int scheduleId,
        CreateRunRequest body,
        CancellationToken ct = default);
}
