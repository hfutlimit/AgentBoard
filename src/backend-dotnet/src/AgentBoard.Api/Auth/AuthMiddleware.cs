// SPDX-License-Identifier: MIT
using System.Security.Claims;
using AgentBoard.Application.Identity;
using Microsoft.AspNetCore.Http;

namespace AgentBoard.Api.Auth;

/// <summary>
/// Resolves the caller from the <c>Authorization: Bearer v1....</c> token
/// issued by <see cref="HmacTokenService"/> and populates
/// <see cref="HttpContext.User"/> so <see cref="CurrentUserService"/> can
/// read it. Anonymous requests pass through untouched, leaving the read-only
/// endpoints open (the .NET BFF only enforces auth on /api/auth/me and write
/// paths). The token is stateless and verified locally — no DB round trip
/// is required for validation.
/// </summary>
public sealed class AuthMiddleware
{
    private const string Scheme = "Bearer ";
    private readonly RequestDelegate _next;
    private readonly ITokenService _tokens;

    public AuthMiddleware(RequestDelegate next, ITokenService tokens)
    {
        _next = next;
        _tokens = tokens;
    }

    public async Task InvokeAsync(HttpContext context)
    {
        var auth = context.Request.Headers.Authorization.ToString();
        if (auth.StartsWith(Scheme, StringComparison.OrdinalIgnoreCase))
        {
            var raw = auth.Substring(Scheme.Length).Trim();
            var uid = _tokens.ValidateToken(raw);
            if (uid is { } id)
            {
                var identity = new ClaimsIdentity(
                    new[] { new Claim("uid", id.ToString(), ClaimValueTypes.Integer32) },
                    "AgentBoardBearer");
                context.User = new ClaimsPrincipal(identity);
            }
        }

        await _next(context);
    }
}
