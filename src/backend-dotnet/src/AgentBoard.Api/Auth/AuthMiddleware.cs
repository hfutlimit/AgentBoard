// SPDX-License-Identifier: MIT
using System.Net.Http;
using System.Net.Http.Headers;
using System.Security.Claims;
using System.Text.Json;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Identity;
using AgentBoard.Domain.Identity;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Configuration;

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
    private readonly IHttpClientFactory _http;
    private readonly IConfiguration _configuration;

    public AuthMiddleware(
        RequestDelegate next,
        ITokenService tokens,
        IHttpClientFactory http,
        IConfiguration configuration)
    {
        _next = next;
        _tokens = tokens;
        _http = http;
        _configuration = configuration;
    }

    public async Task InvokeAsync(
        HttpContext context,
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
                // Credential interop: an ``abk_`` key is authoritative only in
                // FastAPI (the business database). Validate it there via
                // /api/auth/introspect instead of a local, drift-prone shadow
                // copy — this is what makes a FastAPI-valid API key also pass
                // .NET durable auth. Any failure/miss falls through to
                // anonymous (fail closed); .NET's own ApiKeyPermissionMiddleware
                // still enforces api:read / api:write from the mapped claims.
                var actor = await IntrospectViaFastApiAsync(raw, context.RequestAborted);
                if (actor is not null)
                {
                    var claims = new List<Claim>
                    {
                        new("uid", actor.Id.ToString(), ClaimValueTypes.Integer32),
                        new("auth_scheme", "api_key"),
                    };
                    if (!string.IsNullOrEmpty(actor.Username))
                        claims.Add(new Claim("username", actor.Username));
                    claims.Add(new Claim("is_admin", actor.IsAdmin ? "true" : "false"));
                    foreach (var permission in actor.Permissions)
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

    private sealed record IntrospectedActor(
        int Id, string? Username, bool IsAdmin, IReadOnlyList<string> Permissions);

    /// <summary>
    /// Validates an <c>abk_</c> API key against FastAPI (the credential source
    /// of truth) via <c>/api/auth/introspect</c>. Returns <c>null</c> on any
    /// non-success response or transport failure so the caller leaves the
    /// request anonymous (fail closed).
    /// </summary>
    private async Task<IntrospectedActor?> IntrospectViaFastApiAsync(
        string rawKey, CancellationToken cancellationToken)
    {
        try
        {
            var client = _http.CreateClient("AgentBoardFastApi");
            using var request = new HttpRequestMessage(HttpMethod.Get, "api/auth/introspect");
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", rawKey);
            using var response = await client.SendAsync(request, cancellationToken);
            if (!response.IsSuccessStatusCode) return null;

            using var document = JsonDocument.Parse(
                await response.Content.ReadAsStringAsync(cancellationToken));
            var root = document.RootElement;
            if (!root.TryGetProperty("id", out var idEl)
                || !idEl.TryGetInt32(out var id))
            {
                return null;
            }

            var permissions = new List<string>();
            if (root.TryGetProperty("permissions", out var perms)
                && perms.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in perms.EnumerateArray())
                    if (item.ValueKind == JsonValueKind.String && item.GetString() is { } p)
                        permissions.Add(p);
            }

            var username = root.TryGetProperty("username", out var u) && u.ValueKind == JsonValueKind.String
                ? u.GetString()
                : null;
            var isAdmin = root.TryGetProperty("is_admin", out var a)
                && a.ValueKind == JsonValueKind.True;

            return new IntrospectedActor(id, username, isAdmin, permissions);
        }
        catch (Exception)
        {
            // Fail closed: any introspection problem is treated as anonymous.
            return null;
        }
    }
}
