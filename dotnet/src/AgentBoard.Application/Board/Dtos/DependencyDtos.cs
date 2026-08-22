// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Board.Dtos;

/// <summary>Task dependency relationship. Mirrors FastAPI <c>DependencyOut</c>.</summary>
public sealed record TaskDependencyDto(
    int Id,
    int TaskId,
    int DependsOnId,
    string DependencyType,
    DateTime CreatedAt);

/// <summary>Request body for <c>POST /api/tasks/{id}/dependencies</c>.
/// Mirrors FastAPI <c>DependencyCreate</c>.</summary>
public sealed record DependencyCreateRequest(
    int? DependsOnId,
    string? DependencyType);
