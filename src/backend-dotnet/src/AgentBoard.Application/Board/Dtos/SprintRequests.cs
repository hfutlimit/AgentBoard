// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Board.Dtos;

/// <summary>Request body for <c>POST /api/sprints</c>. Dates are ISO-8601 strings; provider parses them.</summary>
public sealed record CreateSprintRequest(
    string? Title,
    string? Goal,
    string? StartDate,
    string? EndDate);

/// <summary>Request body for <c>PATCH /api/sprints/{id}</c>. All fields optional; null = leave unchanged.</summary>
public sealed record UpdateSprintRequest(
    string? Title,
    string? Goal,
    string? Status,
    string? StartDate,
    string? EndDate);
