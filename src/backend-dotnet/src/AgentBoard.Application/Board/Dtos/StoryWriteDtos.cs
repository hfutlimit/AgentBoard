// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Board.Dtos;

/// <summary>Request body for <c>POST /api/stories</c>. Mirrors FastAPI <c>StoryCreate</c>.
/// All properties are nullable so validation happens in the provider layer (422).</summary>
public sealed record StoryCreateRequest(
    string? Title,
    string? Description,
    bool? NeedsDesign);

/// <summary>Request body for <c>PATCH /api/stories/{id}</c>. Mirrors FastAPI <c>StoryPatch</c>.
/// All fields are optional; a null field means "leave unchanged".</summary>
public sealed record StoryPatchRequest(
    string? Title,
    string? Description,
    string? Status,
    bool? NeedsDesign,
    bool? InKanban);

/// <summary>Story status change history entry. Mirrors FastAPI <c>StoryStatusHistoryOut</c>.</summary>
public sealed record StoryStatusHistoryDto(
    int Id,
    string FromStatus,
    string ToStatus,
    int? ChangedBy,
    string? Reason,
    DateTime CreatedAt);
