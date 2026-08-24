// SPDX-License-Identifier: MIT
using System.Security.Claims;

namespace AgentBoard.Api.Auth;

/// <summary>
/// Enforces the permission carried by an API key after <see cref="AuthMiddleware"/>
/// has resolved it. Normal bearer sessions are intentionally unaffected.
/// </summary>
public sealed class ApiKeyPermissionMiddleware
{
    private readonly RequestDelegate _next;

    public ApiKeyPermissionMiddleware(RequestDelegate next) =>
        _next = next ?? throw new ArgumentNullException(nameof(next));

    public async Task InvokeAsync(HttpContext context)
    {
        if (IsApiKey(context.User))
        {
            var required = context.Request.Method is "GET" or "HEAD" or "OPTIONS"
                ? "api:read"
                : "api:write";
            var allowed = context.User.FindAll("api_key_permission")
                .Select(c => c.Value)
                .Contains(required, StringComparer.OrdinalIgnoreCase);

            if (!allowed)
            {
                context.Response.StatusCode = StatusCodes.Status403Forbidden;
                await context.Response.WriteAsJsonAsync(
                    new { detail = $"API key requires {required} permission" },
                    context.RequestAborted);
                return;
            }
        }

        await _next(context);
    }

    private static bool IsApiKey(ClaimsPrincipal principal) =>
        principal.FindFirst("auth_scheme")?.Value == "api_key";
}
