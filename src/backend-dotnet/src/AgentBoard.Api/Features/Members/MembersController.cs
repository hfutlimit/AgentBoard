// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Base;
using AgentBoard.Api.Api.Common;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board;
using AgentBoard.Application.Board.Dtos;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Members;

/// <summary>Project member management. Mirrors FastAPI members router.</summary>
[ApiController]
[Route("api/projects/{projectId:int}/members")]
[Produces("application/json")]
public sealed class MembersController : BaseController<IMemberProvider>
{
    public MembersController(IMemberProvider provider, ICurrentUser current) : base(provider, current) { }

    [HttpPost]
    [ProducesResponseType(typeof(ProjectMemberDto), 201)]
    [ProducesResponseType(typeof(ApiError), 404)]
    [ProducesResponseType(typeof(ApiError), 409)]
    [ProducesResponseType(typeof(ApiError), 422)]
    public async Task<ActionResult<ProjectMemberDto>> Invite(
        int projectId,
        [FromBody] MemberInviteRequest body,
        CancellationToken ct)
    {
        var dto = await Provider.InviteMemberAsync(
            projectId, new InviteMemberRequest(body.UserId, body.Username, body.Role), ct);
        return StatusCode(StatusCodes.Status201Created, dto);
    }

    [HttpDelete("{userId:int}")]
    [ProducesResponseType(typeof(OkResult), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult> Remove(int projectId, int userId, CancellationToken ct)
    {
        var ok = await Provider.RemoveMemberAsync(projectId, userId, ct);
        return ok ? Ok(new { ok = true }) : NotFound(new ApiError($"member user {userId} not found in project {projectId}"));
    }

    [HttpPatch("{userId:int}")]
    [ProducesResponseType(typeof(ProjectMemberDto), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult<ProjectMemberDto>> UpdateRole(
        int projectId, int userId,
        [FromBody] MemberRolePatchRequest body,
        CancellationToken ct)
    {
        var dto = await Provider.UpdateMemberRoleAsync(projectId, userId, body.Role, ct);
        return dto is null ? NotFound(new ApiError($"member user {userId} not found in project {projectId}")) : Ok(dto);
    }
}
