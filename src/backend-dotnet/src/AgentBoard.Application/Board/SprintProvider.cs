// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board.Dtos;
using AgentBoard.Domain.Common;
using AgentBoard.Domain.Entities;

namespace AgentBoard.Application.Board;

/// <summary>Sprint CRUD + lifecycle. Mirrors FastAPI sprints router.</summary>
public sealed class SprintProvider : ISprintProvider
{
    private readonly ISprintRepository _sprints;
    private readonly IProjectRepository _projects;
    private readonly IUnitOfWork _uow;

    public SprintProvider(ISprintRepository sprints, IProjectRepository projects, IUnitOfWork uow)
    {
        _sprints = sprints ?? throw new ArgumentNullException(nameof(sprints));
        _projects = projects ?? throw new ArgumentNullException(nameof(projects));
        _uow = uow ?? throw new ArgumentNullException(nameof(uow));
    }

    public async Task<IReadOnlyList<SprintDto>> ListSprintsAsync(int projectId, CancellationToken ct = default)
    {
        var items = await _sprints.ListAsync(s => s.ProjectId == projectId, ct);
        return items.OrderByDescending(s => s.Id).Select(ToDto).ToList();
    }

    public async Task<SprintDto?> GetSprintAsync(int id, CancellationToken ct = default)
    {
        var s = await _sprints.GetByIdAsync(id, ct);
        return s is null ? null : ToDto(s);
    }

    public async Task<SprintDto> CreateSprintAsync(int projectId, string? title, string? goal, string? startDate, string? endDate, CancellationToken ct = default)
    {
        if (await _projects.GetByIdAsync(projectId, ct) is null)
            throw new NotFoundException($"project {projectId} not found");

        title = (title ?? string.Empty).Trim();
        if (title.Length == 0 || title.Length > 200)
            throw new InvalidValueException("title must be 1-200 characters");

        var sprint = new Sprint
        {
            ProjectId = projectId,
            Title = title,
            Goal = goal ?? string.Empty,
            Status = "planned",
            StartDate = DateTime.TryParse(startDate, out var sd) ? sd : null,
            EndDate = DateTime.TryParse(endDate, out var ed) ? ed : null,
            CreatedAt = DateTime.UtcNow,
        };

        await _sprints.AddAsync(sprint, ct);
        await _uow.SaveChangesAsync(ct);
        return ToDto(sprint);
    }

    public async Task<SprintDto?> UpdateSprintAsync(int id, string? title, string? goal, string? status, string? startDate, string? endDate, CancellationToken ct = default)
    {
        var sprint = await _sprints.GetByIdAsync(id, ct);
        if (sprint is null) return null;

        if (title is not null) sprint.Title = title;
        if (goal is not null) sprint.Goal = goal;
        if (status is not null) sprint.Status = status;
        if (startDate is not null && DateTime.TryParse(startDate, out var sd)) sprint.StartDate = sd;
        if (endDate is not null && DateTime.TryParse(endDate, out var ed)) sprint.EndDate = ed;

        _sprints.Update(sprint);
        await _uow.SaveChangesAsync(ct);
        return ToDto(sprint);
    }

    public async Task<bool> DeleteSprintAsync(int id, CancellationToken ct = default)
    {
        var sprint = await _sprints.GetByIdAsync(id, ct);
        if (sprint is null) return false;
        _sprints.Remove(sprint);
        await _uow.SaveChangesAsync(ct);
        return true;
    }

    public async Task<SprintDto?> ActivateSprintAsync(int id, CancellationToken ct = default)
    {
        var sprint = await _sprints.GetByIdAsync(id, ct);
        if (sprint is null) return null;
        sprint.Status = "active";
        _sprints.Update(sprint);
        await _uow.SaveChangesAsync(ct);
        return ToDto(sprint);
    }

    public async Task<SprintDto?> CompleteSprintAsync(int id, CancellationToken ct = default)
    {
        var sprint = await _sprints.GetByIdAsync(id, ct);
        if (sprint is null) return null;
        sprint.Status = "completed";
        _sprints.Update(sprint);
        await _uow.SaveChangesAsync(ct);
        return ToDto(sprint);
    }

    private static SprintDto ToDto(Sprint s) =>
        new(s.Id, s.ProjectId, s.Title, s.Goal, s.Status, s.StartDate, s.EndDate, s.CreatedAt);
}
