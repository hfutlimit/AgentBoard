// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Board.Dtos;

/// <summary>Sprint record. Mirrors FastAPI <c>SprintOut</c>.</summary>
public sealed record SprintDto(
    int Id,
    int ProjectId,
    string Title,
    string Goal,
    string Status,
    DateTime? StartDate,
    DateTime? EndDate,
    DateTime CreatedAt);

/// <summary>Request body for <c>POST /api/sprints</c>. Mirrors FastAPI <c>SprintIn</c>.</summary>
public sealed record SprintCreateRequest(
    string? Title,
    string? Goal,
    string? StartDate,
    string? EndDate);

/// <summary>Request body for <c>PATCH /api/sprints/{id}</c>. All fields optional.</summary>
public sealed record SprintPatchRequest(
    string? Title,
    string? Goal,
    string? Status,
    string? StartDate,
    string? EndDate);
