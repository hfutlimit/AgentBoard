// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Board.Dtos;

/// <summary>Request body for <c>POST /api/projects</c>. All fields optional except validation in provider.</summary>
public sealed record CreateProjectRequest(
    string? Name,
    string? Key,
    string? Description);

/// <summary>Request body for <c>PATCH /api/projects/{id}</c>. All fields optional; null = leave unchanged.</summary>
public sealed record UpdateProjectRequest(
    string? Name,
    string? Key,
    string? Description,
    bool? IsPrivate,
    bool? IsArchived);

/// <summary>Request body for <c>PATCH /api/stories/{id}</c>. All fields optional; null = leave unchanged.</summary>
public sealed record UpdateStoryRequest(
    string? Title,
    string? Description,
    string? Status,
    bool? NeedsDesign,
    bool? InKanban);

/// <summary>Request body for <c>POST /api/comments</c>. Exactly one of TaskId/StoryId/EpicId must be set.</summary>
public sealed record CreateCommentRequest(
    int? TaskId,
    int? StoryId,
    int? EpicId,
    string? Author,
    string? Content);

/// <summary>Query parameters for <c>GET /api/tasks/search</c>.</summary>
public sealed record SearchTasksQuery(
    string? Q,
    int? ProjectId,
    int? StoryId,
    string? Status,
    string? Priority,
    string? AssigneeId,
    int? Limit);

/// <summary>Query parameters for unified project ticket list (Epics + Stories + Tasks).</summary>
public sealed record ListProjectTicketsQuery(
    string? StatusFilter,
    string? Sort,
    string? Order,
    int? Limit,
    int? Offset);

/// <summary>Query parameters for project center listing (scope, sort, paging, archive).</summary>
public sealed record ListProjectsCenterQuery(
    bool IsAdmin,
    string? Scope,
    string? Sort,
    int? Limit,
    int? Offset,
    bool? IncludeArchived);

/// <summary>Query parameters for basic project listing (paging + archive).</summary>
public sealed record ListProjectsQuery(
    int? Limit,
    int? Offset,
    bool? IncludeArchived);

/// <summary>Query parameters for extended project listing (paging + archive, scoped to current user).</summary>
public sealed record ListProjectsExtendedQuery(
    int? Limit,
    int? Offset,
    bool? IncludeArchived);

/// <summary>Request body for <c>POST /api/projects/{id}/members</c>. Provide either UserId or Username.</summary>
public sealed record InviteMemberRequest(
    int? UserId,
    string? Username,
    string? Role);

/// <summary>Request body for <c>POST /api/projects/{id}/sprints</c> (workspace Sprint tab).</summary>
public sealed record CreateProjectSprintRequest(
    string? Title,
    string? Goal,
    DateTime? StartDate,
    DateTime? EndDate);

/// <summary>Request body for <c>POST /api/projects/{id}/schedules</c> (workspace Schedules tab).</summary>
public sealed record CreateProjectScheduleRequest(
    string? Title,
    string? ScheduleType,
    string? CronExpr);
