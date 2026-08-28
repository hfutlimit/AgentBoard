// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board.Dtos;
using AgentBoard.Application.Scheduling.Dtos;
using AgentBoard.Domain.Common;

namespace AgentBoard.Application.Board;

/// <summary>
/// Read-only query surface for the board domain. Mirrors the FastAPI GET
/// routers 1:1 so the S0-5 contract-freeze tests pass unchanged. All reads
/// go through the FastAPI-owned tables via the read-only repositories.
/// </summary>
public interface IBoardProvider : IProvider
{
	Task<ProjectListResult> ListProjectsAsync(
		int limit, int offset, bool? includeArchived, CancellationToken ct = default);
	Task<ProjectDto?> GetProjectAsync(int id, CancellationToken ct = default);

	// ---- P2: write operations (mirrors FastAPI projects router) ----

	/// <summary>
	/// Create a project. <see cref="CreateProjectRequest.Name"/> is required (1-200);
	/// <see cref="CreateProjectRequest.Key"/> is optional and truncated to 20; <c>is_private</c>
	/// is forced true (FastAPI rule). When <paramref name="currentUserId"/> is set, the creator
	/// is added as owner. Throws <see cref="InvalidValueException"/> on bad input,
	/// <see cref="DuplicateException"/> on a duplicate key. Returns the created project (201).
	/// </summary>
	Task<ProjectDto> CreateProjectAsync(CreateProjectRequest request, int? currentUserId, CancellationToken ct = default);

	/// <summary>
	/// Patch a project. Returns null when not found (404).
	/// </summary>
	Task<ProjectDto?> UpdateProjectAsync(int id, UpdateProjectRequest request, CancellationToken ct = default);

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
	Task<CommentDto> CreateCommentAsync(CreateCommentRequest request, CancellationToken ct = default);

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
	Task<StoryDto?> UpdateStoryAsync(int id, UpdateStoryRequest request, CancellationToken ct = default);
	Task<bool> DeleteStoryAsync(int id, CancellationToken ct = default);
	Task<StoryDto?> ConfirmStoryAsync(int id, CancellationToken ct = default);
	Task<StoryDto?> CompleteStoryAsync(int id, CancellationToken ct = default);
	Task<IReadOnlyList<StoryStatusHistoryDto>> GetStoryStatusHistoryAsync(int id, CancellationToken ct = default);

	// ---- P3: Task writes ----

	Task<TaskItemDto> CreateTaskAsync(int storyId, TaskCreateRequest request, CancellationToken ct = default);
	Task<TaskItemDto?> UpdateTaskAsync(int id, TaskPatchRequest request, CancellationToken ct = default);
	Task<bool> DeleteTaskAsync(int id, CancellationToken ct = default);
	Task<TaskItemDto?> UpdateTaskStatusAsync(int id, string? status, string? statusReason, CancellationToken ct = default);
	Task<IReadOnlyList<TaskItemDto>> BulkUpdateTasksAsync(BulkTaskUpdateRequest request, CancellationToken ct = default);
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

	Task<IReadOnlyList<TaskItemDto>> SearchTasksAsync(SearchTasksQuery query, CancellationToken ct = default);

	// ---- P3: project extensions ----

	/// <summary>Extended project list with pagination and archive filter.</summary>
	Task<IReadOnlyList<ProjectDto>> ListProjectsExtendedAsync(
		ListProjectsExtendedQuery query, int? currentUserId, CancellationToken ct = default);

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
		int projectId, ListProjectTicketsQuery query, CancellationToken ct = default);

	/// <summary>Projects the user is a member of.</summary>
	Task<IReadOnlyList<ProjectDto>> ListUserProjectsAsync(int userId, string? role, CancellationToken ct = default);

	// ---- P3: member management ----

	/// <summary>Invite a member to a project.</summary>
	Task<ProjectMemberDto> InviteMemberAsync(
		int projectId, InviteMemberRequest request, CancellationToken ct = default);

	/// <summary>Remove a member from a project.</summary>
	Task<bool> RemoveMemberAsync(int projectId, int userId, CancellationToken ct = default);

	/// <summary>Update a member's role.</summary>
	Task<ProjectMemberDto?> UpdateMemberRoleAsync(int projectId, int userId, string? role, CancellationToken ct = default);

	// ---- P3: review stats ----

	/// <summary>Get review statistics for a project.</summary>
	Task<ReviewStatsDto?> GetReviewStatsAsync(int projectId, int days, int? userId, CancellationToken ct = default);

	// ---- P6: workspace nested creation (BFF module 6, 2026-08-23) ----

	/// <summary>Create a Story under a specific Epic. Returns null when the epic is missing (404).</summary>
	Task<StoryDto?> CreateEpicStoryAsync(int epicId, string? title, string? description, CancellationToken ct = default);

	/// <summary>List tasks for a story with optional status filter. Returns items + total for paging.</summary>
	Task<(IReadOnlyList<TaskItemDto> Items, int Total)> ListStoryTasksAsync(
		int storyId, string? status, int limit, int offset, CancellationToken ct = default);

	/// <summary>Create a task directly under a story (skips the per-story context endpoint).</summary>
	Task<TaskItemDto?> CreateStoryTaskAsync(
		int storyId, TaskCreateUnderStoryRequest request, CancellationToken ct = default);

	// ---- P1: project center / workspace nested creation (BFF module 1, 2026-08-23) ----

	/// <summary>List projects in the project center with scope filter (active | archived | all | mine | created) + sort.</summary>
	Task<ProjectsCenterResult> ListProjectsCenterAsync(
		ListProjectsCenterQuery query, int? currentUserId, CancellationToken ct = default);

	/// <summary>List epics for a specific project. Used by workspace Epics tab.</summary>
	Task<IReadOnlyList<EpicDto>> ListProjectEpicsAsync(int projectId, string? status, int limit, int offset, CancellationToken ct = default);

	/// <summary>Create an epic under a specific project.</summary>
	Task<EpicDto?> CreateProjectEpicAsync(int projectId, string? title, string? description, CancellationToken ct = default);

	/// <summary>Create a sprint under a specific project (workspace Sprint tab).</summary>
	Task<SprintDto?> CreateProjectSprintAsync(
		int projectId, CreateProjectSprintRequest request, CancellationToken ct = default);

	/// <summary>List schedules for a specific project (workspace Schedules tab).</summary>
	Task<IReadOnlyList<AgentScheduleDto>> ListProjectSchedulesAsync(
		int projectId, int limit, int offset, CancellationToken ct = default);

	Task<AgentScheduleDto?> CreateProjectScheduleAsync(
		int projectId, CreateProjectScheduleRequest request, int? currentUserId, CancellationToken ct = default);

	/// <summary>Export the full project tree (project + epics + stories + tasks) for backup / migration.</summary>
	Task<ProjectExportDto?> ExportProjectAsync(int projectId, CancellationToken ct = default);

	/// <summary>Import a project tree from <see cref="ProjectImportRequest"/>. Returns count summary.</summary>
	Task<ProjectImportResult?> ImportProjectAsync(int targetProjectId, ProjectImportRequest body, CancellationToken ct = default);

	// ---- P5: sprint burndown + sprint tasks (BFF module 5, 2026-08-23) ----

	/// <summary>Get the burndown chart data for a sprint (ideal vs actual remaining per day).</summary>
	Task<SprintBurndownDto?> GetSprintBurndownAsync(int sprintId, int days, CancellationToken ct = default);

	/// <summary>List tasks belonging to a sprint, paged.</summary>
	Task<(IReadOnlyList<TaskItemDto> Items, int Total)> ListSprintTasksAsync(
		int sprintId, string? status, int limit, int offset, CancellationToken ct = default);
}
