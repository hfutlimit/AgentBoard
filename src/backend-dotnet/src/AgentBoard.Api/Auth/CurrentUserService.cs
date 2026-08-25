// SPDX-License-Identifier: MIT
using System.Security.Claims;
using AgentBoard.Application.Abstractions;
using Microsoft.AspNetCore.Http;

namespace AgentBoard.Api.Auth;

/// <summary>
/// Default <see cref="ICurrentUser"/> implementation. Reads the caller from
/// the <see cref="ClaimsPrincipal"/> that <see cref="AuthMiddleware"/> builds
/// from the bearer token (or API key) and now populates the
/// <c>username</c> / <c>is_admin</c> claims at auth time (P0-2), so this
/// service no longer needs a DB fallback for those fields. Anonymous
/// requests expose a null <see cref="UserId"/>.
/// </summary>
public sealed class CurrentUserService : ICurrentUser
{
    private readonly IHttpContextAccessor _http;

    public CurrentUserService(IHttpContextAccessor http) =>
        _http = http ?? throw new ArgumentNullException(nameof(http));

    private ClaimsPrincipal? Principal => _http.HttpContext?.User;

    public int? UserId => UserIdFromPrincipal(Principal);
    public string? Username => Principal?.FindFirstValue("username");
    public bool IsAdmin => bool.TryParse(Principal?.FindFirstValue("is_admin"), out var b) && b;

    /// <summary>
    /// API key permissions declared at token-issue time. <see cref="AuthMiddleware"/>
    /// emits one <c>api_key_permission</c> claim per scope; we read them all
    /// here. Anonymous requests yield an empty array.
    /// </summary>
    public IReadOnlyList<string> ApiKeyPermissions =>
        Principal?.FindAll("api_key_permission").Select(c => c.Value).ToArray()
            ?? Array.Empty<string>();

    /// <summary>Reads the uid claim set by <see cref="AuthMiddleware"/>.</summary>
    public static int? UserIdFromPrincipal(ClaimsPrincipal? principal)
    {
        var v = principal?.FindFirstValue("uid");
        return int.TryParse(v, out var id) ? id : null;
    }
}
