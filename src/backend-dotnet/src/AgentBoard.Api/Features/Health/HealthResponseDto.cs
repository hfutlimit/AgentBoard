// SPDX-License-Identifier: MIT
namespace AgentBoard.Api.Features.Health;

/// <summary>
/// Shape of GET /api/health. Mirrors FastAPI's
/// <c>features/admin/router.py::health()</c> 1:1 — <c>status</c>,
/// <c>database</c>, <c>version</c>, <c>timestamp</c>.
/// </summary>
public sealed record HealthResponseDto(
    string Status,
    string Database,
    string Version,
    DateTime Timestamp);
