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

    // ---- P2: write operations (mirrors FastAPI projects router) ----

    /// <summary>
    /// Create a project. <paramref name="name"/> is required (1-200); <paramref name="key"/>
    /// is optional and truncated to 20; <c>is_private</c> is forced true (FastAPI rule).
    /// When <paramref name="currentUserId"/> is set, the creator is added as owner.
    /// Throws <see cref="InvalidValueException"/> on bad input, <see cref="DuplicateException"/>
    /// on a duplicate key. Returns the created project (201).
    /// </summary>
    Task<ProjectDto> CreateProjectAsync(string? name, string? key, string? description, int? currentUserId, CancellationToken ct = default);

    /// <summary>
    /// Patch a project (name/key/description/is_private/is_archived). Returns null when not found (404).
    /// </summary>
    Task<ProjectDto?> UpdateProjectAsync(int id, string? name, string? key, string? description, bool? isPrivate, bool? isArchived, CancellationToken ct = default);

    /// <summary>Delete a project and its board hierarchy. Returns false when not found (404).</summary>
    Task<bool> DeleteProjectAsync(int id, CancellationToken ct = default);

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

    // ---- P3: Epic writes ----

    Task<EpicDto> CreateEpicAsync(int projectId, string? title, string? description, CancellationToken ct = default);
    Task<EpicDto?> UpdateEpicAsync(int id, string? title, string? description, string? status, CancellationToken ct = default);
    Task<bool> DeleteEpicAsync(int id, CancellationToken ct = default);

    // ---- P3: Story writes ----

    Task<StoryDto> CreateStoryAsync(int epicId, string? title, string? description, bool? needsDesign, CancellationToken ct = default);
    Task<StoryDto?> UpdateStoryAsync(int id, string? title, string? description, string? status, bool? needsDesign, bool? inKanban, CancellationToken ct = default);
    Task<bool> DeleteStoryAsync(int id, CancellationToken ct = default);
    Task<StoryDto?> ConfirmStoryAsync(int id, CancellationToken ct = default);
    Task<StoryDto?> CompleteStoryAsync(int id, CancellationToken ct = default);
    Task<IReadOnlyList<StoryStatusHistoryDto>> GetStoryStatusHistoryAsync(int id, CancellationToken ct = default);

    // ---- P3: Task writes ----

    Task<TaskItemDto> CreateTaskAsync(int storyId, string? type, string? title, string? priority, string? description, string? spec, int? assigneeId, CancellationToken ct = default);
    Task<TaskItemDto?> UpdateTaskAsync(int id, string? type, string? title, string? status, string? priority, string? statusReason, string? description, string? spec, int? assigneeId, string? dueDate, string? labels, double? estimate, int? complexity, string? neededCapabilities, string? domainTags, int? sprintId, int? reviewerId, CancellationToken ct = default);
    Task<bool> DeleteTaskAsync(int id, CancellationToken ct = default);
    Task<TaskItemDto?> UpdateTaskStatusAsync(int id, string? status, string? statusReason, CancellationToken ct = default);
    Task<IReadOnlyList<TaskItemDto>> BulkUpdateTasksAsync(List<int>? taskIds, string? status, string? priority, int? assigneeId, string? dueDate, CancellationToken ct = default);
    Task<int> BulkDeleteTasksAsync(List<int>? taskIds, CancellationToken ct = default);

    // ---- P3: Task dependencies ----

    Task<IReadOnlyList<TaskDependencyDto>> GetTaskDependenciesAsync(int taskId, CancellationToken ct = default);
    Task<TaskDependencyDto> AddTaskDependencyAsync(int taskId, int? dependsOnId, string? dependencyType, CancellationToken ct = default);
    Task<bool> RemoveTaskDependencyAsync(int dependencyId, CancellationToken ct = default);

    // ---- P3: Attachments (read-only; upload handled by FastAPI) ----

    Task<IReadOnlyList<AttachmentDto>> ListAttachmentsAsync(int taskId, CancellationToken ct = default);
    Task<AttachmentDto?> GetAttachmentInfoAsync(int attachmentId, CancellationToken ct = default);
    Task<bool> DeleteAttachmentAsync(int attachmentId, CancellationToken ct = default);

    // ---- P3: Search extensions ----

    Task<IReadOnlyList<TaskItemDto>> SearchTasksAsync(string? q, int? projectId, int? storyId, string? status, string? priority, string? assigneeId, int limit, CancellationToken ct = default);

    // ---- P3: project extensions ----

    /// <summary>Extended project list with pagination and archive filter.</summary>
    Task<IReadOnlyList<ProjectDto>> ListProjectsExtendedAsync(
        int limit, int offset, bool? includeArchived, int? currentUserId, CancellationToken ct = default);

    /// <summary>Archive a project (set IsArchived=true).</summary>
    Task<ProjectDto?> ArchiveProjectAsync(int id, CancellationToken ct = default);

    /// <summary>Unarchive a project (set IsArchived=false).</summary>
    Task<ProjectDto?> UnarchiveProjectAsync(int id, CancellationToken ct = default);

    /// <summary>Bulk archive projects by id list.</summary>
    Task<int> BulkArchiveProjectsAsync(List<int>? ids, CancellationToken ct = default);

    /// <summary>Bulk unarchive projects by id list.</summary>
    Task<int> BulkUnarchiveProjectsAsync(List<int>? ids, CancellationToken ct = default);

    /// <summary>Unified ticket list for a project (Epics + Stories + Tasks).</summary>
    Task<TicketListResult> ListProjectTicketsAsync(
        int projectId, string statusFilter, string sort, string order,
        int limit, int offset, CancellationToken ct = default);

    /// <summary>Projects the user is a member of.</summary>
    Task<IReadOnlyList<ProjectDto>> ListUserProjectsAsync(int userId, string? role, CancellationToken ct = default);

    // ---- P3: member management ----

    /// <summary>Invite a member to a project.</summary>
    Task<ProjectMemberDto> InviteMemberAsync(
        int projectId, int? userId, string? username, string? role, CancellationToken ct = default);

    /// <summary>Remove a member from a project.</summary>
    Task<bool> RemoveMemberAsync(int projectId, int userId, CancellationToken ct = default);

    /// <summary>Update a member's role.</summary>
    Task<ProjectMemberDto?> UpdateMemberRoleAsync(int projectId, int userId, string? role, CancellationToken ct = default);

    // ---- P3: review stats ----

    /// <summary>Get review statistics for a project.</summary>
    Task<ReviewStatsDto?> GetReviewStatsAsync(int projectId, int days, int? userId, CancellationToken ct = default);
}
