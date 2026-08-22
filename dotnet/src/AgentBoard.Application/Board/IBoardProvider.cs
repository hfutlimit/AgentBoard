// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board.Dtos;
using AgentBoard.Domain.Common;

namespace AgentBoard.Application.Board;

/// <summary>
/// Read-only query surface for the board domain. Mirrors the FastAPI GET
/// routers 1:1 so the S0-5 contract-freeze tests pass unchanged. All reads
/// go through the FastAPI-owned tables via the read-only repositories.
/// </summary>
public interface IBoardProvider : IProvider
{
    Task<IReadOnlyList<ProjectDto>> ListProjectsAsync(CancellationToken ct = default);
    Task<ProjectDto?> GetProjectAsync(int id, CancellationToken ct = default);

    Task<IReadOnlyList<EpicDto>> ListEpicsAsync(int? projectId, CancellationToken ct = default);
    Task<EpicDto?> GetEpicAsync(int id, CancellationToken ct = default);

    Task<IReadOnlyList<StoryDto>> ListStoriesAsync(int? epicId, CancellationToken ct = default);
    Task<StoryDto?> GetStoryAsync(int id, CancellationToken ct = default);

    Task<IReadOnlyList<TaskItemDto>> ListTasksAsync(int? projectId, int? storyId, CancellationToken ct = default);
    Task<TaskItemDto?> GetTaskAsync(int id, CancellationToken ct = default);

    Task<IReadOnlyList<CommentDto>> ListCommentsAsync(
        int? taskId, int? storyId, int? epicId, CancellationToken ct = default);
    Task<CommentDto?> GetCommentAsync(int id, CancellationToken ct = default);

    // ---- P2: write operations (mirrors FastAPI work_items comment router) ----

    /// <summary>
    /// Create a comment attached to exactly one of Task / Story / Epic.
    /// Throws <see cref="InvalidValueException"/> when the target is ambiguous
    /// or author/content is empty, <see cref="NotFoundException"/> when the
    /// target row does not exist. Returns the persisted comment (201).
    /// </summary>
    Task<CommentDto> CreateCommentAsync(
        int? taskId, int? storyId, int? epicId, string? author, string? content, CancellationToken ct = default);

    /// <summary>Delete a comment by id. Returns false when not found (404).</summary>
    Task<bool> DeleteCommentAsync(int id, CancellationToken ct = default);

    // ---- P1: dashboard / board reads (mirrors FastAPI aggregation endpoints) ----

    /// <summary>Cross-project overview. Admin sees all; member sees own; anon sees empty.</summary>
    Task<OverviewDto> GetOverviewAsync(int? currentUserId, bool isAdmin, CancellationToken ct = default);

    Task<ProjectStatsDto?> GetProjectStatsAsync(int projectId, CancellationToken ct = default);

    Task<KanbanDto?> GetProjectKanbanAsync(int projectId, bool includeAll, CancellationToken ct = default);

    Task<ProjectMembersResult?> ListProjectMembersAsync(int projectId, int limit, int offset, CancellationToken ct = default);

    Task<NotificationsResult> ListNotificationsAsync(int userId, int limit, int offset, bool unreadOnly, CancellationToken ct = default);

    Task<int> GetUnreadNotificationCountAsync(int userId, CancellationToken ct = default);
}
