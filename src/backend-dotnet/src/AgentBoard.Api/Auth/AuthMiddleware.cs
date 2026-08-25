// SPDX-License-Identifier: MIT
using System.Security.Claims;
using System.Text.Json;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Identity;
using AgentBoard.Domain.Identity;
using Microsoft.AspNetCore.Http;

namespace AgentBoard.Api.Auth;

/// <summary>
/// Resolves the caller from the <c>Authorization: Bearer v1....</c> token
/// issued by <see cref="HmacTokenService"/> (or from an <c>abk_</c> API
/// key) and populates <see cref="HttpContext.User"/> so
/// <see cref="CurrentUserService"/> can read it. Anonymous requests pass
/// through untouched, leaving the read-only endpoints open (the .NET BFF
/// only enforces auth on /api/auth/me and write paths). The bearer token
/// is stateless and verified locally — no DB round trip is required for
/// validation. API keys are validated against the unique <c>key_hash</c>
/// index via <see cref="IApiKeyRepository.GetByHashAsync"/>.
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

    public async Task InvokeAsync(
        HttpContext context,
        IApiKeyRepository apiKeys,
        IUserRepository users)
    {
        var auth = context.Request.Headers.Authorization.ToString();
        if (string.IsNullOrWhiteSpace(auth)
            && context.Request.Path.StartsWithSegments("/hubs")
            && context.Request.Query.TryGetValue("access_token", out var accessToken)
            && !string.IsNullOrWhiteSpace(accessToken))
        {
            auth = $"Bearer {accessToken}";
        }
        if (auth.StartsWith(Scheme, StringComparison.OrdinalIgnoreCase))
        {
            var raw = auth.Substring(Scheme.Length).Trim();
            if (raw.StartsWith("abk_", StringComparison.Ordinal))
            {
                // P0-3: single-row index seek via the unique key_hash column
                // instead of the previous ListAsync(predicate) scan. We still
                // do an in-memory `Enabled` check so disabled keys are
                // rejected even if a stale hash somehow matched.
                var digest = Convert.ToHexString(
                    System.Security.Cryptography.SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(raw)))
                    .ToLowerInvariant();
                var key = await apiKeys.GetByHashAsync(digest, context.RequestAborted);
                if (key is { Enabled: true })
                {
                    var user = await users.GetByIdAsync(key.UserId, context.RequestAborted);
                    var claims = new List<Claim>
                    {
                        new("uid", key.UserId.ToString(), ClaimValueTypes.Integer32),
                        new("auth_scheme", "api_key"),
                    };
                    if (user is not null)
                    {
                        // P0-2: stamp username + is_admin so CurrentUserService
                        // does not have to round-trip the DB on every read.
                        claims.Add(new Claim("username", user.Username));
                        claims.Add(new Claim("is_admin", user.IsAdmin ? "true" : "false"));
                    }
                    foreach (var permission in ParseScopes(key.Scopes))
                        claims.Add(new Claim("api_key_permission", permission));
                    context.User = new ClaimsPrincipal(new ClaimsIdentity(claims, "AgentBoardApiKey"));
                }
            }
            else
            {
                var uid = _tokens.ValidateToken(raw);
                if (uid is { } id)
                {
                    var user = await users.GetByIdAsync(id, context.RequestAborted);
                    var claims = new List<Claim>
                    {
                        new("uid", id.ToString(), ClaimValueTypes.Integer32),
                    };
                    if (user is not null)
                    {
                        // P0-2: same as the api_key branch — populate the
                        // identity claims that CurrentUserService exposes
                        // (Username, IsAdmin) so middleware and downstream
                        // code never sees null where a real value exists.
                        claims.Add(new Claim("username", user.Username));
                        claims.Add(new Claim("is_admin", user.IsAdmin ? "true" : "false"));
                    }
                    var identity = new ClaimsIdentity(claims, "AgentBoardBearer");
                    context.User = new ClaimsPrincipal(identity);
                }
            }
        }

        await _next(context);
    }

    private static IReadOnlyList<string> ParseScopes(string? raw)
    {
        if (string.IsNullOrWhiteSpace(raw)) return Array.Empty<string>();
        try
        {
            return JsonSerializer.Deserialize<string[]>(raw) ?? Array.Empty<string>();
        }
        catch (JsonException)
        {
            return Array.Empty<string>();
        }
    }
}
