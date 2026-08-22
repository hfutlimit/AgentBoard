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
