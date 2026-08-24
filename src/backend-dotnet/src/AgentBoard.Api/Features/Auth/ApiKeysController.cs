// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Base;
using AgentBoard.Api.Api.Common;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board;
using AgentBoard.Application.Board.Dtos;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Auth;

/// <summary>API key management. Mirrors FastAPI <c>/api/api-keys</c>.</summary>
[ApiController]
[Route("api/api-keys")]
[Produces("application/json")]
public sealed class ApiKeysController : BaseController<IApiKeyProvider>
{
    public ApiKeysController(IApiKeyProvider provider, ICurrentUser current) : base(provider, current) { }

    [HttpGet]
    [ProducesResponseType(typeof(IReadOnlyList<ApiKeyDto>), 200)]
    [ProducesResponseType(typeof(ApiError), 401)]
    public async Task<ActionResult<IReadOnlyList<ApiKeyDto>>> List(CancellationToken ct)
    {
        var uid = CurrentUser.UserId;
        if (uid is null) return Problem(StatusCodes.Status401Unauthorized, "authentication required");
        return Ok(await Provider.ListApiKeysAsync(uid.Value, ct));
    }

    [HttpPost]
    [ProducesResponseType(typeof(ApiKeyCreatedResponse), 201)]
    [ProducesResponseType(typeof(ApiError), 401)]
    [ProducesResponseType(typeof(ApiError), 422)]
    public async Task<ActionResult<ApiKeyCreatedResponse>> Create(
        [FromBody] ApiKeyCreateRequest body, CancellationToken ct)
    {
        var uid = CurrentUser.UserId;
        if (uid is null) return Problem(StatusCodes.Status401Unauthorized, "authentication required");
        var (dto, rawKey) = await Provider.CreateApiKeyAsync(uid.Value, body.Name, body.Scopes, ct);
        var response = new ApiKeyCreatedResponse(dto.Id, dto.Name, dto.KeyPrefix, dto.Scopes, dto.Enabled, dto.CreatedAt, rawKey);
        return StatusCode(StatusCodes.Status201Created, response);
    }

    [HttpDelete("{id:int}")]
    [ProducesResponseType(typeof(object), 200)]
    [ProducesResponseType(typeof(ApiError), 401)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult> Delete(int id, CancellationToken ct)
    {
        var uid = CurrentUser.UserId;
        if (uid is null) return Problem(StatusCodes.Status401Unauthorized, "authentication required");
        var ok = await Provider.DeleteApiKeyAsync(id, uid.Value, ct);
        return ok ? Ok(new { ok = true }) : NotFound(new ApiError($"api key {id} not found"));
    }
}
