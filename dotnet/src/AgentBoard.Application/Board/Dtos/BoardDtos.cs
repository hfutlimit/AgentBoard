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
