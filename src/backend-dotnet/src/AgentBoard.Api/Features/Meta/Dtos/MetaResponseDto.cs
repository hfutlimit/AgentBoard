// SPDX-License-Identifier: MIT
using System.Text.Json.Serialization;

namespace AgentBoard.Api.Features.Meta.Dtos;

/// <summary>
/// Shape of GET /api/meta. Mirrors FastAPI's
/// <c>features/admin/router.py::meta()</c> 1:1.
///
/// The string values are the **lowercase enum names** used by the Python
/// side (e.g. <c>ItemType.DEV = "dev"</c>). Order is preserved from the
/// <c>ALL_*</c> lists in <c>agentboard/core/common/enums.py</c>.
///
/// Field names use <c>[JsonPropertyName]</c> to lock them to snake_case —
/// FastAPI / Pydantic do not transform the source identifier, so the wire
/// format keeps underscores. The default ASP.NET Core 10
/// System.Text.Json would otherwise emit camelCase and break the
/// contract-freeze test in S0-5.
///
/// Note: the values here intentionally diverge from the .NET
/// <c>AgentBoard.Domain.Common.Enums</c> enums. The Domain enums are
/// the EF Core persistence-side representation; the Meta endpoint
/// is a public API contract and must stay aligned with FastAPI even
/// if the internal enum shapes eventually drift. When they do, this
/// DTO is updated alongside an OpenAPI snapshot regen.
/// </summary>
public sealed record MetaResponseDto(
    [property: JsonPropertyName("types")]           IReadOnlyList<string> Types,
    [property: JsonPropertyName("statuses")]         IReadOnlyList<string> Statuses,
    [property: JsonPropertyName("priorities")]       IReadOnlyList<string> Priorities,
    [property: JsonPropertyName("sprint_statuses")]  IReadOnlyList<string> SprintStatuses,
    [property: JsonPropertyName("schedule_types")]   IReadOnlyList<string> ScheduleTypes,
    [property: JsonPropertyName("run_statuses")]     IReadOnlyList<string> RunStatuses);
