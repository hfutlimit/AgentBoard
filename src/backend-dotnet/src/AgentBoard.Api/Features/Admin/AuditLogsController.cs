// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Base;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board;
using AgentBoard.Domain.Entities;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Admin;

/// <summary>Audit log queries. Mirrors FastAPI <c>/api/audit-logs</c>.</summary>
[ApiController]
[Route("api/audit-logs")]
[Produces("application/json")]
public sealed class AuditLogsController : BaseController<IAuditProvider>
{
    public AuditLogsController(IAuditProvider provider, ICurrentUser current) : base(provider, current) { }

    [HttpGet]
    [ProducesResponseType(typeof(IReadOnlyList<AuditLog>), 200)]
    public async Task<ActionResult<IReadOnlyList<AuditLog>>> List(
        [FromQuery(Name = "entity_type")] string? entityType,
        [FromQuery(Name = "entity_id")] int? entityId,
        [FromQuery(Name = "user_id")] int? userId,
        [FromQuery] string? action,
        [FromQuery] int limit = 50,
        [FromQuery] int offset = 0,
        CancellationToken ct = default)
    {
        if (!CurrentUser.IsAdmin)
            return Problem(StatusCodes.Status403Forbidden, "admin access required");
        return Ok(await Provider.ListAuditLogsAsync(entityType, entityId, userId, action, limit, offset, ct));
    }
}
