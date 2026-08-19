// SPDX-License-Identifier: MIT
using AgentBoard.Api.Features.Meta.Dtos;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Meta;

/// <summary>
/// Returns the public enum vocabularies the AgentBoard web app / SDK
/// uses to populate dropdowns and validate inputs. The values mirror
/// FastAPI exactly (see <c>agentboard/features/admin/router.py::meta()</c>).
///
/// Stage 0 only: the strings are hard-coded. Stage 1 (after the .NET
/// Domain enums are aligned with FastAPI) will switch this to read
/// the values from a single source of truth — likely a generated
/// constants file synced via the OpenAPI contract-freeze pipeline.
/// </summary>
[ApiController]
[Route("api/meta")]
[Produces("application/json")]
public sealed class MetaController : ControllerBase
{
    [HttpGet]
    [ProducesResponseType(typeof(MetaResponseDto), StatusCodes.Status200OK)]
    public ActionResult<MetaResponseDto> Get() => Ok(new MetaResponseDto(
        Types:          new[] { "dev", "bug", "qa", "design" },
        Statuses:        new[] { "todo", "in_progress", "in_review", "done", "blocked" },
        Priorities:      new[] { "highest", "high", "medium", "low", "lowest" },
        SprintStatuses:  new[] { "planning", "active", "completed" },
        ScheduleTypes:   new[] { "once", "cron" },
        RunStatuses:     new[] { "pending", "running", "success", "failed", "cancelled" }));
}
