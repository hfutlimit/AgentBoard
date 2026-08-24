// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Board.Dtos;

/// <summary>Unified ticket item aggregating Epics, Stories, and Tasks for a project.</summary>
public sealed record TicketItem(
    string Type,
    int Id,
    string Title,
    string Status,
    string? Description,
    DateTime CreatedAt,
    DateTime UpdatedAt,
    string? AssigneeName);

/// <summary>Paginated ticket list result.</summary>
public sealed record TicketListResult(IReadOnlyList<TicketItem> Items, int Total);
