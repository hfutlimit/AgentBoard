// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Board.Dtos;

/// <summary>Unified search result item across entity types.</summary>
public sealed record SearchResultItem(
    string Type,
    int Id,
    string Title,
    string? Hint,
    int? ProjectId,
    string? Status,
    DateTime UpdatedAt);
