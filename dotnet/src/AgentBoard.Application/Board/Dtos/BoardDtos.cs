// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Board.Dtos;

public sealed record ProjectDto(
    int Id,
    string Name,
    string? Key,
    string Description,
    bool IsPrivate,
    DateTime CreatedAt,
    bool IsArchived);

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
