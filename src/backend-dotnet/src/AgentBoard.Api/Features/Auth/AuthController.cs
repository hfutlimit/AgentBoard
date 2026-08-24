// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Base;
using AgentBoard.Application.Identity;
using AgentBoard.Application.Identity.Dtos;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Auth;

/// <summary>
/// Auth endpoints. Routes match the FastAPI <c>/api/auth/*</c> paths 1:1
/// so the contract-freeze tests in S0-5 pass without translation.
/// </summary>
[ApiController]
[Route("api/auth")]
[Produces("application/json")]
public sealed class AuthController : BaseController<IAuthProvider>
{
    public AuthController(IAuthProvider provider, Application.Abstractions.ICurrentUser current)
        : base(provider, current) { }

    [HttpPost("login")]
    [ProducesResponseType(typeof(AuthSessionDto), StatusCodes.Status200OK)]
    [ProducesResponseType(typeof(Api.Common.ApiError), StatusCodes.Status422UnprocessableEntity)]
    public async Task<ActionResult<AuthSessionDto>> Login(
        [FromBody] LoginRequest request, CancellationToken ct) =>
        Ok(await Provider.LoginAsync(request.Username, request.Password, ct));

    [HttpGet("me")]
    [ProducesResponseType(typeof(UserDto), StatusCodes.Status200OK)]
    [ProducesResponseType(typeof(Api.Common.ApiError), StatusCodes.Status404NotFound)]
    public async Task<ActionResult<UserDto>> Me(CancellationToken ct)
    {
        if (CurrentUser.UserId is null)
            return Problem(StatusCodes.Status401Unauthorized, "authentication required");
        return Ok(await Provider.GetCurrentAsync(CurrentUser.UserId.Value, ct));
    }

    [HttpPost("change-password")]
    [ProducesResponseType(StatusCodes.Status204NoContent)]
    [ProducesResponseType(typeof(Api.Common.ApiError), StatusCodes.Status422UnprocessableEntity)]
    public async Task<IActionResult> ChangePassword(
        [FromBody] ChangePasswordRequest request, CancellationToken ct)
    {
        if (CurrentUser.UserId is null)
            return Problem(StatusCodes.Status401Unauthorized, "authentication required");
        await Provider.ChangePasswordAsync(
            CurrentUser.UserId.Value, request.CurrentPassword, request.NewPassword, ct);
        return NoContent();
    }

    [HttpPost("register")]
    [ProducesResponseType(typeof(AuthSessionDto), StatusCodes.Status201Created)]
    [ProducesResponseType(typeof(Api.Common.ApiError), StatusCodes.Status422UnprocessableEntity)]
    [ProducesResponseType(typeof(Api.Common.ApiError), StatusCodes.Status409Conflict)]
    public async Task<ActionResult<AuthSessionDto>> Register(
        [FromBody] RegisterRequest request, CancellationToken ct)
    {
        var session = await Provider.RegisterAsync(request.Username, request.Password, ct);
        return StatusCode(StatusCodes.Status201Created, session);
    }

    [HttpPatch("me")]
    [ProducesResponseType(typeof(UserDto), StatusCodes.Status200OK)]
    [ProducesResponseType(typeof(Api.Common.ApiError), StatusCodes.Status401Unauthorized)]
    public async Task<ActionResult<UserDto>> UpdateProfile(
        [FromBody] UpdateProfileRequest request, CancellationToken ct)
    {
        if (CurrentUser.UserId is null)
            return Problem(StatusCodes.Status401Unauthorized, "authentication required");
        return Ok(await Provider.UpdateProfileAsync(
            CurrentUser.UserId.Value, request.DisplayName, request.Email, request.AvatarUrl, ct));
    }
}

/// <summary>Payload for POST /api/auth/change-password.</summary>
public sealed record ChangePasswordRequest(string CurrentPassword, string NewPassword);

/// <summary>Payload for POST /api/auth/register.</summary>
public sealed record RegisterRequest(string? Username, string? Password);

/// <summary>Payload for PATCH /api/auth/me.</summary>
public sealed record UpdateProfileRequest(string? DisplayName, string? Email, string? AvatarUrl);
