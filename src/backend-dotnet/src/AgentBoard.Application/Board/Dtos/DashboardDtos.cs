// SPDX-License-Identifier: MIT
using System.Text.Json.Serialization;

namespace AgentBoard.Application.Board.Dtos;

/// <summary>Cross-project dashboard aggregate. Mirrors FastAPI <c>/api/overview</c>.</summary>
public sealed record OverviewCounts(int Projects, int Epics, int Stories, int Tasks, int DoneTasks);

public sealed record OverviewProjectProgress(int Id, string Name, int Total, int Done, int Percent);

public sealed record StatusCount(string Status, int Count);

public sealed record DayCount(string Day, int Count);

public sealed record OverviewDto(
    OverviewCounts Counts,
    IReadOnlyList<OverviewProjectProgress> Projects,
    IReadOnlyList<StatusCount> StatusDistribution,
    [property: JsonPropertyName("activity_7d")] IReadOnlyList<DayCount> Activity7d);

/// <summary>Per-project task statistics. Mirrors FastAPI <c>/api/projects/{pid}/stats</c>.</summary>
public sealed record ProjectStatsDto(
    int Total, int Done, int Backlog, int Active,
    IReadOnlyList<DayCount> DailyCreated,
    IReadOnlyList<DayCount> DailyDone);

/// <summary>Kanban board. Mirrors FastAPI <c>/api/projects/{pid}/kanban</c>.</summary>
public sealed record KanbanTaskDto(
    int Id, string Type, string Title, string Status, string Priority, int? AssigneeId, double? Estimate);

public sealed record KanbanStoryDto(
    int Id, int EpicId, string Title, string Description, string Status,
    bool NeedsDesign, bool InKanban, IReadOnlyList<KanbanTaskDto> Tasks, DateTime CreatedAt);

public sealed record KanbanDto(
    IReadOnlyDictionary<string, IReadOnlyList<KanbanStoryDto>> Columns,
    IReadOnlyList<KanbanStoryDto> Items);

/// <summary>Project member (with joined username). Mirrors FastAPI <c>/api/projects/{pid}/members</c>.</summary>
public sealed record ProjectMemberDto(int Id, int ProjectId, int UserId, string Role, DateTime JoinedAt, string? Username);

public sealed record ProjectMembersResult(IReadOnlyList<ProjectMemberDto> Items, int Total);

/// <summary>Notification. Mirrors FastAPI <c>/api/notifications</c>.</summary>
public sealed record NotificationDto(
    int Id, int UserId, string Type, string Title, string Content, bool IsRead, string? Link, DateTime CreatedAt);

public sealed record NotificationsResult(IReadOnlyList<NotificationDto> Items, int Total);
