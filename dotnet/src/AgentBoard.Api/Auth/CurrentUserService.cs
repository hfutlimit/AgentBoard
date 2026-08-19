// SPDX-License-Identifier: MIT
using System.Security.Claims;
using AgentBoard.Application.Abstractions;
using Microsoft.AspNetCore.Http;

namespace AgentBoard.Api.Auth;

/// <summary>
/// Default <see cref="ICurrentUser"/> implementation. Reads the
/// <c>X-User-Id</c>, <c>X-User-Name</c>, <c>X-Is-Admin</c> and
/// <c>X-Api-Key-Permissions</c> headers that the <c>AuthMiddleware</c>
/// (lands in S0-7) is expected to populate. In stage 0 the headers are
/// optional — when missing, the service reports an anonymous caller.
/// </summary>
public sealed class CurrentUserService : ICurrentUser
{
    private readonly IHttpContextAccessor _http;

    public CurrentUserService(IHttpContextAccessor http) =>
        _http = http ?? throw new ArgumentNullException(nameof(http));

    public int? UserId => ParseInt(_http.HttpContext?.Request.Headers["X-User-Id"]);
    public string? Username => _http.HttpContext?.Request.Headers["X-User-Name"].ToString();
    public bool IsAdmin => bool.TryParse(_http.HttpContext?.Request.Headers["X-Is-Admin"], out var b) && b;
    public IReadOnlyList<string> ApiKeyPermissions =>
        (_http.HttpContext?.Request.Headers["X-Api-Key-Permissions"].ToString() ?? "")
            .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);

    private static int? ParseInt(string? s) =>
        int.TryParse(s, out var v) ? v : null;

    /// <summary>Convenience for the auth middleware: read uid from the
    /// <see cref="ClaimsPrincipal"/> it just built.</summary>
    public static int? UserIdFromPrincipal(ClaimsPrincipal? principal) =>
        principal is null ? null : ParseInt(principal.FindFirstValue("uid"));
}
