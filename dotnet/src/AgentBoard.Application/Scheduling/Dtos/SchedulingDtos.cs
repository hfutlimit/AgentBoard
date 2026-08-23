// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Scheduling.Dtos;

// ===== Agent registry (Stage 2 module 2) =====
//
// Mirrors FastAPI's `agents` table + `_ser` projection (see
// `agentboard/features/projects/models.py:Agent._PUBLIC_FIELDS`). The .NET
// DTOs are intentionally permissive: `roles` / `capabilities` are stored as
// JSON list strings (e.g. `["reviewer"]`); callers may pass a JSON array
// string OR a Python-style list of strings/dicts and the provider will
// normalize to JSON before persistence.

/// <summary>
/// Wire contract for an Agent registry row. Field names are emitted in
/// snake_case by the API's global JSON policy — matches the FastAPI
/// <c>Agent._ser</c> output so the Angular frontend can consume the .NET
/// BFF and FastAPI interchangeably.
/// </summary>
public sealed record AgentDto(
    int Id,
    string AgentId,
    string Name,
    string Roles,
    string Capabilities,
    string CliCommand,
    string Model,
    string AuthKey,
    int? UserId,
    bool Online,
    bool Enabled,
    DateTime? LastHeartbeat,
    string ProbeMessage,
    DateTime? LastProbeAt,
    DateTime CreatedAt,
    DateTime UpdatedAt);

/// <summary>Request body for <c>POST /api/agents/register</c>.</summary>
public sealed record AgentRegisterRequest(
    string? AgentId,
    string? Name,
    string? Description,
    string? Roles,
    string? Capabilities,
    string? CliCommand,
    string? Model,
    string? AuthKey,
    int? UserId);

/// <summary>Request body for <c>PUT /api/agents/{agentId}</c>. All fields optional.</summary>
public sealed record AgentUpdateRequest(
    string? Name,
    string? Roles,
    string? Capabilities,
    string? CliCommand,
    string? Model,
    string? AuthKey,
    bool? Enabled,
    int? UserId);

/// <summary>Request body for <c>POST /api/agents/{agentId}/probe</c> (optional override).</summary>
public sealed record AgentProbeRequest(
    int? Timeout);

/// <summary>
/// Response body for <c>POST /api/agents/{agentId}/probe</c>. The
/// <c>status</c> field is the only contract surface (online | offline);
/// <c>latency_ms</c> is best-effort.
/// </summary>
public sealed record AgentProbeResponse(
    string Status,
    long? LatencyMs,
    string? Message,
    DateTime ProbedAt);

// ===== Schedule / Run DTOs (Stage 2 module 4) =====
//
// `AgentScheduleDto` / `AgentRunDto` mirror the entity field-for-field;
// `SchedulePatchRequest` / `CreateRunRequest` are the write bodies
// accepted by PATCH /api/schedules/{id} and POST /api/schedules/{id}/runs
// respectively. Every field is nullable on the request side so callers
// can do partial updates / minimal kick-offs.

public sealed record AgentScheduleDto(
    int Id,
    int ProjectId,
    string Title,
    string ScheduleType,
    string? CronExpr,
    string? Agent,
    int? TaskId,
    string? TaskPriority,
    string? TaskType,
    int? EpicId,
    bool Enabled,
    DateTime? NextRunAt,
    DateTime? LastRunAt,
    DateTime CreatedAt,
    DateTime UpdatedAt,
    int? CreatedBy,
    int? UpdatedBy);

/// <summary>Partial update body for <c>PATCH /api/schedules/{id}</c>. Only
/// non-null fields are written.</summary>
public sealed record SchedulePatchRequest(
    string? Title,
    string? ScheduleType,
    string? CronExpr,
    string? Agent,
    int? TaskId,
    string? TaskPriority,
    string? TaskType,
    int? EpicId,
    bool? Enabled);

public sealed record AgentRunDto(
    int Id,
    int ScheduleId,
    int? TaskId,
    int? AgentRegistryId,
    int? AssignmentId,
    string? Agent,
    string? Model,
    string Status,
    string? IdempotencyKey,
    DateTime? StartedAt,
    DateTime? FinishedAt,
    string? Output,
    string? ErrorMessage,
    string? Summary,
    string? LogRef,
    DateTime CreatedAt);

/// <summary>Body for <c>POST /api/schedules/{id}/runs</c> (manual retry).</summary>
public sealed record CreateRunRequest(
    string? IdempotencyKey,
    int? TaskId,
    string? Agent);
