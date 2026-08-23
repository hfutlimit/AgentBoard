// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Board.Dtos;

public sealed record ProjectDto(
    int Id,
    string Name,
    string? Key,
    string Description,
    bool IsPrivate,
    DateTime CreatedAt,
    bool IsArchived,
    int? TaskCount = null,
    int? TaskDone = null);

public sealed record EpicDto(
    int Id,
    int ProjectId,
    string Title,
    string Description,
    string Status,
    DateTime CreatedAt);

public sealed record StoryDto(
    int Id,
    int EpicId,
    string Title, string Description,
    string Status,
    bool NeedsDesign,
    int? ReviewerId,
    int ReviewRound,
    bool InKanban,
    DateTime CreatedAt);

public sealed record TaskItemDto(
    int Id,
    int ProjectId,
    int? StoryId,
    string Type,
    string Title,
    string Status,
    string Priority,
    string? StatusReason,
    string Description,
    int? AssigneeId,
    DateTime? DueDate,
    string? Labels,
    double? Estimate,
    int? Complexity,
    DateTime CreatedAt,
    DateTime UpdatedAt);

public sealed record CommentDto(
    int Id,
    int? TaskId,
    int? StoryId,
    int? EpicId,
    string Author,
    string Content,
    DateTime CreatedAt,
    DateTime UpdatedAt);

/// <summary>Request body for <c>POST /api/{tasks|stories|epics}/{id}/comments</c>. Mirrors FastAPI <c>CommentIn</c>.
/// Properties are nullable so a missing field reaches the provider (which throws <see cref="AgentBoard.Domain.Common.InvalidValueException"/>
/// → 422), matching FastAPI's Pydantic <c>min_length=1</c> validation rather than ASP.NET's implicit [Required] → 400.</summary>
public sealed record CommentCreateRequest(
    string? Author,
    string? Content);

/// <summary>Request body for <c>POST /api/projects</c>. Mirrors FastAPI <c>ProjectIn</c>.
/// Properties are nullable so a missing <c>name</c> reaches the provider (which throws
/// <see cref="AgentBoard.Domain.Common.InvalidValueException"/> → 422), matching FastAPI's
/// Pydantic <c>min_length=1</c> validation rather than ASP.NET's implicit [Required] → 400.</summary>
public sealed record ProjectCreateRequest(
    string? Name,
    string? Key,
    string? Description);

/// <summary>Request body for <c>PATCH /api/projects/{id}</c>. Mirrors FastAPI <c>ProjectPatchExtended</c>.
/// All fields are optional; a null field means "leave unchanged".</summary>
public sealed record ProjectPatchRequest(
    string? Name,
    string? Key,
    string? Description,
    bool? IsPrivate,
    bool? IsArchived);

// ===== P6: project center / export / import (BFF module 1, 2026-08-23) =====

/// <summary>Response for <c>GET /api/projects/center</c> with scope+sort+limit
/// filtering. Mirrors the FastAPI <c>/api/projects/center</c> envelope.</summary>
public sealed record ProjectsCenterResult(
    IReadOnlyList<ProjectDto> Items,
    IReadOnlyList<ProjectDto> Page,
    int Total,
    string Scope,
    string Sort);

/// <summary>Response for <c>GET /api/projects/{id}/export</c>. Includes the
/// project itself plus its child epics / stories / tasks for offline restore.</summary>
public sealed record ProjectExportDto(
    ProjectDto Project,
    IReadOnlyList<EpicDto> Epics,
    IReadOnlyList<StoryDto> Stories,
    IReadOnlyList<TaskItemDto> Tasks,
    DateTime ExportedAt);

/// <summary>Response for <c>POST /api/projects/{id}/import</c>. Reports
/// counts of imported vs error rows so the UI can show a summary.</summary>
public sealed record ProjectImportResult(
    int ProjectCreated,
    int EpicsImported,
    int StoriesImported,
    int TasksImported,
    int Imported,
    int Errors,
    IReadOnlyList<string> ErrorMessages);

/// <summary>Request body for <c>POST /api/projects/{id}/import</c>.</summary>
public sealed record ProjectImportRequest(
    ProjectDto Project,
    IReadOnlyList<EpicDto>? Epics,
    IReadOnlyList<StoryDto>? Stories,
    IReadOnlyList<TaskItemDto>? Tasks);

/// <summary>Response for <c>GET /api/sprints/{id}/burndown</c>. Linear ideal
/// vs actual remaining for each day of the sprint window.</summary>
public sealed record SprintBurndownDto(
    int SprintId,
    int TotalTasks,
    IReadOnlyList<SprintBurndownPoint> Days,
    double CurrentBurnRate);

public sealed record SprintBurndownPoint(
    DateTime Date,
    int IdealRemaining,
    int ActualRemaining);
