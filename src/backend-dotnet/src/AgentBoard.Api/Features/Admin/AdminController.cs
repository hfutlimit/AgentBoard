// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Base;
using AgentBoard.Api.Api.Common;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board;
using AgentBoard.Application.Board.Dtos;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Admin;

/// <summary>Admin endpoints. Mirrors FastAPI <c>/api/admin</c>.</summary>
[ApiController]
[Route("api/admin")]
[Produces("application/json")]
public sealed class AdminController : BaseController<IAdminProvider>
{
    public AdminController(IAdminProvider provider, ICurrentUser current) : base(provider, current) { }

    [HttpGet("users")]
    [ProducesResponseType(typeof(IReadOnlyList<AdminUserDto>), 200)]
    [ProducesResponseType(typeof(ApiError), 403)]
    public async Task<ActionResult<IReadOnlyList<AdminUserDto>>> ListUsers(CancellationToken ct)
    {
        if (!CurrentUser.IsAdmin)
            return Problem(StatusCodes.Status403Forbidden, "admin access required");
        return Ok(await Provider.ListUsersAsync(ct));
    }

    [HttpPatch("users/{userId:int}")]
    [ProducesResponseType(typeof(object), 200)]
    [ProducesResponseType(typeof(ApiError), 403)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult> SetUserAdmin(
        int userId, [FromBody] SetAdminRequest body, CancellationToken ct)
    {
        if (!CurrentUser.IsAdmin)
            return Problem(StatusCodes.Status403Forbidden, "admin access required");
        var ok = await Provider.SetUserAdminAsync(userId, body.IsAdmin, ct);
        return ok ? Ok(new { ok = true }) : NotFound(new ApiError($"user {userId} not found"));
    }

    [HttpGet("projects")]
    [ProducesResponseType(typeof(IReadOnlyList<AdminProjectDto>), 200)]
    [ProducesResponseType(typeof(ApiError), 403)]
    public async Task<ActionResult<IReadOnlyList<AdminProjectDto>>> ListProjects(CancellationToken ct)
    {
        if (!CurrentUser.IsAdmin)
            return Problem(StatusCodes.Status403Forbidden, "admin access required");
        return Ok(await Provider.ListAllProjectsAsync(ct));
    }

    [HttpDelete("projects/{projectId:int}")]
    [ProducesResponseType(typeof(object), 200)]
    [ProducesResponseType(typeof(ApiError), 403)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult> DeleteProject(int projectId, CancellationToken ct)
    {
        if (!CurrentUser.IsAdmin)
            return Problem(StatusCodes.Status403Forbidden, "admin access required");
        var ok = await Provider.DeleteProjectAsync(projectId, ct);
        return ok ? Ok(new { ok = true }) : NotFound(new ApiError($"project {projectId} not found"));
    }
}

public sealed record SetAdminRequest(bool IsAdmin);
