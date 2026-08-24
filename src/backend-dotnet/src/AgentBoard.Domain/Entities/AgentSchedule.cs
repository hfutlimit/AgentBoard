// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Entities;

/// <summary>
/// AgentSchedule aggregate. Maps to the <c>agent_schedules</c> table that
/// the FastAPI/Alembic operator owns. The .NET BFF is a read+write shim
/// for PATCH/DELETE that landed in Stage 2 — the create flow stays
/// FastAPI-side via <c>POST /api/projects/{id}/schedules</c> for now.
///
/// Stage 2 module 4: BFF gains the 4 schedule/runs write endpoints
/// (PATCH/DELETE schedule + list/create run) so the front-end Agent
/// configuration center can mutate without round-tripping to Python.
/// </summary>
public sealed class AgentSchedule : Entity, IAuditableEntity
{
    public int ProjectId { get; set; }
    public string Title { get; set; } = string.Empty;
    public string ScheduleType { get; set; } = "cron";
    public string? CronExpr { get; set; }
    public string? Agent { get; set; }
    public int? TaskId { get; set; }
    public string? TaskPriority { get; set; }
    public string? TaskType { get; set; }
    public int? EpicId { get; set; }
    public bool Enabled { get; set; } = true;
    public DateTime? NextRunAt { get; set; }
    public DateTime? LastRunAt { get; set; }
    public DateTime CreatedAt { get; set; }
    public DateTime UpdatedAt { get; set; }
    public int? CreatedBy { get; set; }
    public int? UpdatedBy { get; set; }
}
