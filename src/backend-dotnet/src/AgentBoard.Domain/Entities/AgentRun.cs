// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Entities;

/// <summary>
/// AgentRun aggregate. Maps to the <c>agent_runs</c> table that the
/// FastAPI/Alembic operator owns. A run is the execution-time record
/// produced by a schedule (or manual retry) and carries the agent /
/// model snapshot so historical rows stay meaningful even after the
/// schedule configuration changes.
///
/// Stage 2 module 4: the .NET BFF gains <c>POST /api/schedules/{id}/runs</c>
/// (manual retry kick-off) and <c>GET /api/schedules/{id}/runs</c>
/// (paged history). The actual background executor remains Python-side;
/// the .NET endpoint only enqueues a new row with status='running' (the
/// executor picks it up and calls back to the Python service to update
/// the final state).
/// </summary>
public sealed class AgentRun : Entity
{
    public int ScheduleId { get; set; }
    public int? TaskId { get; set; }
    public int? AgentRegistryId { get; set; }
    public int? AssignmentId { get; set; }
    public string? Agent { get; set; }
    public string? Model { get; set; }
    public string Status { get; set; } = "pending";
    public string? IdempotencyKey { get; set; }
    public DateTime? StartedAt { get; set; }
    public DateTime? FinishedAt { get; set; }
    public string? Output { get; set; }
    public string? ErrorMessage { get; set; }
    public string? Summary { get; set; }
    public string? LogRef { get; set; }
    public DateTime CreatedAt { get; set; }
}
