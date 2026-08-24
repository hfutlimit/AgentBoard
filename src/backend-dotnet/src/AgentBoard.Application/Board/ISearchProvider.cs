// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board.Dtos;

namespace AgentBoard.Application.Board;

/// <summary>Global search across entity types. Mirrors FastAPI search router.</summary>
public interface ISearchProvider : IProvider
{
    Task<IReadOnlyList<SearchResultItem>> SearchStoriesAsync(string? q, int? projectId, int limit, CancellationToken ct = default);
    Task<IReadOnlyList<SearchResultItem>> SearchEpicsAsync(string? q, int? projectId, int limit, CancellationToken ct = default);
    Task<IReadOnlyList<SearchResultItem>> SearchSprintsAsync(string? q, int? projectId, int limit, CancellationToken ct = default);
    Task<IReadOnlyList<SearchResultItem>> SearchAgentsAsync(string? q, int limit, CancellationToken ct = default);
    Task<IReadOnlyList<SearchResultItem>> SearchNotificationsAsync(string? q, int userId, int limit, CancellationToken ct = default);
    Task<IReadOnlyList<SearchResultItem>> SearchProposalsAsync(string? q, int? userId, int limit, CancellationToken ct = default);
    Task<IReadOnlyList<SearchResultItem>> SearchTicketsAsync(string? q, int? projectId, int limit, CancellationToken ct = default);
    Task<IReadOnlyList<SearchResultItem>> SearchSchedulesAsync(string? q, int? projectId, int limit, CancellationToken ct = default);
    Task<IReadOnlyList<SearchResultItem>> SearchRunsAsync(string? q, int? projectId, int limit, CancellationToken ct = default);
}
