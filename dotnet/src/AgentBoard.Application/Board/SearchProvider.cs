// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board.Dtos;
using AgentBoard.Domain.Common;

namespace AgentBoard.Application.Board;

/// <summary>Global search across entity types. Mirrors FastAPI search router.</summary>
public sealed class SearchProvider : ISearchProvider
{
    private readonly IStoryRepository _stories;
    private readonly IEpicRepository _epics;
    private readonly ISprintRepository _sprints;
    private readonly INotificationRepository _notifications;

    public SearchProvider(
        IStoryRepository stories,
        IEpicRepository epics,
        ISprintRepository sprints,
        INotificationRepository notifications)
    {
        _stories = stories ?? throw new ArgumentNullException(nameof(stories));
        _epics = epics ?? throw new ArgumentNullException(nameof(epics));
        _sprints = sprints ?? throw new ArgumentNullException(nameof(sprints));
        _notifications = notifications ?? throw new ArgumentNullException(nameof(notifications));
    }

    public async Task<IReadOnlyList<SearchResultItem>> SearchStoriesAsync(string? q, int? projectId, int limit, CancellationToken ct = default)
    {
        var items = await _stories.ListAsync(ct: ct);
        if (projectId.HasValue) items = items.Where(s => s.EpicId == projectId.Value).ToList();
        if (!string.IsNullOrWhiteSpace(q))
            items = items.Where(s => s.Title.Contains(q, StringComparison.OrdinalIgnoreCase)).ToList();
        return items.Take(limit).Select(s => new SearchResultItem("story", s.Id, s.Title, s.Description, null, s.Status, s.CreatedAt)).ToList();
    }

    public async Task<IReadOnlyList<SearchResultItem>> SearchEpicsAsync(string? q, int? projectId, int limit, CancellationToken ct = default)
    {
        var items = await _epics.ListAsync(ct: ct);
        if (projectId.HasValue) items = items.Where(e => e.ProjectId == projectId.Value).ToList();
        if (!string.IsNullOrWhiteSpace(q))
            items = items.Where(e => e.Title.Contains(q, StringComparison.OrdinalIgnoreCase)).ToList();
        return items.Take(limit).Select(e => new SearchResultItem("epic", e.Id, e.Title, e.Description, e.ProjectId, e.Status, e.CreatedAt)).ToList();
    }

    public async Task<IReadOnlyList<SearchResultItem>> SearchSprintsAsync(string? q, int? projectId, int limit, CancellationToken ct = default)
    {
        var items = await _sprints.ListAsync(ct: ct);
        if (projectId.HasValue) items = items.Where(s => s.ProjectId == projectId.Value).ToList();
        if (!string.IsNullOrWhiteSpace(q))
            items = items.Where(s => s.Title.Contains(q, StringComparison.OrdinalIgnoreCase)).ToList();
        return items.Take(limit).Select(s => new SearchResultItem("sprint", s.Id, s.Title, s.Goal, s.ProjectId, s.Status, s.CreatedAt)).ToList();
    }

    public Task<IReadOnlyList<SearchResultItem>> SearchAgentsAsync(string? q, int limit, CancellationToken ct = default) =>
        Task.FromResult<IReadOnlyList<SearchResultItem>>(Array.Empty<SearchResultItem>());

    public async Task<IReadOnlyList<SearchResultItem>> SearchNotificationsAsync(string? q, int userId, int limit, CancellationToken ct = default)
    {
        var items = await _notifications.ListAsync(n => n.UserId == userId, ct);
        if (!string.IsNullOrWhiteSpace(q))
            items = items.Where(n => n.Title.Contains(q, StringComparison.OrdinalIgnoreCase)).ToList();
        return items.Take(limit).Select(n => new SearchResultItem("notification", n.Id, n.Title, n.Content, null, null, n.CreatedAt)).ToList();
    }

    public Task<IReadOnlyList<SearchResultItem>> SearchProposalsAsync(string? q, int? userId, int limit, CancellationToken ct = default) =>
        Task.FromResult<IReadOnlyList<SearchResultItem>>(Array.Empty<SearchResultItem>());

    public Task<IReadOnlyList<SearchResultItem>> SearchTicketsAsync(string? q, int? projectId, int limit, CancellationToken ct = default) =>
        Task.FromResult<IReadOnlyList<SearchResultItem>>(Array.Empty<SearchResultItem>());

    public Task<IReadOnlyList<SearchResultItem>> SearchSchedulesAsync(string? q, int? projectId, int limit, CancellationToken ct = default) =>
        Task.FromResult<IReadOnlyList<SearchResultItem>>(Array.Empty<SearchResultItem>());

    public Task<IReadOnlyList<SearchResultItem>> SearchRunsAsync(string? q, int? projectId, int limit, CancellationToken ct = default) =>
        Task.FromResult<IReadOnlyList<SearchResultItem>>(Array.Empty<SearchResultItem>());
}
