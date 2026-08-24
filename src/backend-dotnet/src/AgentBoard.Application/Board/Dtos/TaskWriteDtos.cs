// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Board.Dtos;

/// <summary>Request body for <c>POST /api/tasks</c>. Mirrors FastAPI <c>TaskCreate</c>.
/// All properties are nullable so validation happens in the provider layer (422),
/// matching FastAPI's Pydantic validation pattern.</summary>
public sealed record TaskCreateRequest(
	int? ProjectId,
	int? StoryId,
	string? Type,
	string? Title,
	string? Status,
	string? Priority,
	string? Description,
	string? Spec,
	int? AssigneeId,
	string? DueDate,
	string? Labels,
	double? Estimate,
	int? Complexity,
	string? NeededCapabilities,
	string? DomainTags);

/// <summary>Request body for <c>PATCH /api/tasks/{id}</c>. Mirrors FastAPI <c>TaskPatch</c>.
/// All fields are optional; a null field means "leave unchanged".</summary>
public sealed record TaskPatchRequest(
	int? ProjectId,
	int? StoryId,
	string? Type,
	string? Title,
	string? Status,
	string? Priority,
	string? StatusReason,
	string? Description,
	string? Spec,
	int? AssigneeId,
	string? DueDate,
	string? Labels,
	double? Estimate,
	int? Complexity,
	string? NeededCapabilities,
	string? DomainTags,
	int? SprintId,
	int? ReviewerId);

/// <summary>Request body for <c>PUT /api/tasks/{id}/status</c>.
/// Validates the status transition server-side.</summary>
public sealed record TaskStatusRequest(
	string? Status,
	string? StatusReason);

/// <summary>Request body for <c>PATCH /api/tasks/bulk</c> (batch status/priority update).</summary>
public sealed record BulkTaskUpdateRequest(
	List<int>? TaskIds,
	string? Status,
	string? Priority,
	int? AssigneeId,
	string? DueDate);

// ===== P6: AI task generation proxy (BFF module 6, 2026-08-23) =====
//
// `GenerateSubtasksRequest` is retained for wire compatibility while the BFF
// proxies the FastAPI source-of-truth endpoint. `StoryTasksPage` is
// a thin `items + total` wrapper (the broader `PagedResult<T>` lives in the
// frontend models and the existing `TicketListResult` is project-scoped, so
// keeping a dedicated record here avoids pulling a generic PagedResult
// into the Application layer just for this endpoint).

public sealed record GenerateSubtasksRequest(int? Count);

public sealed record StoryTasksPage(
	IReadOnlyList<TaskItemDto> Items,
	int Total);

/// <summary>Body for <c>POST /api/stories/{storyId}/tasks</c> (create task under story).</summary>
public sealed record TaskCreateUnderStoryRequest(
	string? Title,
	string? Type,
	string? Priority,
	string? Description,
	int? AssigneeId);
