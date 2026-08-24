// SPDX-License-Identifier: MIT
using System.Security.Claims;
using System.Text.Json;
using AgentBoard.Application.Abstractions;
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

    public async Task InvokeAsync(HttpContext context, IApiKeyRepository apiKeys)
    {
        var auth = context.Request.Headers.Authorization.ToString();
        if (auth.StartsWith(Scheme, StringComparison.OrdinalIgnoreCase))
        {
            var raw = auth.Substring(Scheme.Length).Trim();
            if (raw.StartsWith("abk_", StringComparison.Ordinal))
            {
                var digest = Convert.ToHexString(
                    System.Security.Cryptography.SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(raw)))
                    .ToLowerInvariant();
                var prefix = raw[..Math.Min(12, raw.Length)];
                var keys = await apiKeys.ListAsync(
                    k => k.Enabled && k.KeyPrefix == prefix && k.KeyHash == digest,
                    context.RequestAborted);
                var key = keys.FirstOrDefault();
                if (key is not null)
                {
                    var claims = new List<Claim>
                    {
                        new("uid", key.UserId.ToString(), ClaimValueTypes.Integer32),
                        new("auth_scheme", "api_key"),
                    };
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
                    var identity = new ClaimsIdentity(
                        new[] { new Claim("uid", id.ToString(), ClaimValueTypes.Integer32) },
                        "AgentBoardBearer");
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
