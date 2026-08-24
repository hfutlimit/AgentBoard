// SPDX-License-Identifier: MIT
using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;
using AgentBoard.Api.Tests.Infrastructure;
using FluentAssertions;
using Microsoft.Extensions.DependencyInjection;
using AgentBoard.Infrastructure.Persistence;
using Microsoft.EntityFrameworkCore;

namespace AgentBoard.Api.Tests.Features;

/// <summary>
/// Integration tests for <c>POST /api/auth/register</c>, <c>POST /api/auth/login</c>
/// and <c>GET /api/auth/me</c>. The register path is the regression for the
/// BFF stage-0.5 bug where <c>UserConfiguration.b.Ignore(UpdatedAt)</c> combined
/// with EnsureCreated still emitted a NOT NULL <c>updated_at</c> column, so
/// every EF insert into <c>users</c> threw
/// <c>SQLite Error 19: NOT NULL constraint failed: users.updated_at</c>
/// (commit 88fc556 fix was incomplete).
/// </summary>
public sealed class AuthControllerTests : IClassFixture<ApiWebApplicationFactory>
{
    private readonly ApiWebApplicationFactory _factory;

    public AuthControllerTests(ApiWebApplicationFactory factory) => _factory = factory;

    // The BFF serialises responses in snake_case (FastAPI wire-format parity);
    // pin the matching property names here so the test stays in lock-step with
    // the API contract.
    private sealed record RegisterRequest(
        [property: JsonPropertyName("username")] string? Username,
        [property: JsonPropertyName("password")] string? Password);
    private sealed record LoginRequest(
        [property: JsonPropertyName("username")] string? Username,
        [property: JsonPropertyName("password")] string? Password);
    private sealed record AuthSession(
        [property: JsonPropertyName("id")] int Id,
        [property: JsonPropertyName("username")] string Username,
        [property: JsonPropertyName("token")] string Token,
        [property: JsonPropertyName("token_type")] string TokenType,
        [property: JsonPropertyName("expires_in")] int ExpiresIn);
    private sealed record UserDto(
        [property: JsonPropertyName("id")] int Id,
        [property: JsonPropertyName("username")] string Username,
        [property: JsonPropertyName("display_name")] string? DisplayName,
        [property: JsonPropertyName("email")] string? Email,
        [property: JsonPropertyName("avatar_url")] string? AvatarUrl,
        [property: JsonPropertyName("is_admin")] bool IsAdmin,
        [property: JsonPropertyName("created_at")] DateTime CreatedAt,
        [property: JsonPropertyName("updated_at")] DateTime UpdatedAt);

    private static readonly JsonSerializerOptions SnakeCaseJson = new()
    {
        PropertyNameCaseInsensitive = true,
    };

    [Fact]
    public async Task Register_CreatesUser_AndReturnsV1Token()
    {
        using var client = _factory.CreateClient();
        var uname = "register_" + Guid.NewGuid().ToString("N")[..8];
        var resp = await client.PostAsJsonAsync("/api/auth/register",
            new RegisterRequest(uname, "TestPass1234"));

        resp.StatusCode.Should().Be(HttpStatusCode.Created);
        var session = await resp.Content.ReadFromJsonAsync<AuthSession>(SnakeCaseJson);
        session.Should().NotBeNull();
        session!.Username.Should().Be(uname);
        session.TokenType.Should().Be("bearer");
        session.Token.Should().StartWith("v1.");
    }

    [Fact]
    public async Task Register_ThenLogin_ThenMe_RoundTrips()
    {
        using var client = _factory.CreateClient();
        var uname = "rt_" + Guid.NewGuid().ToString("N")[..8];
        var pw = "TestPass1234";

        var reg = await client.PostAsJsonAsync("/api/auth/register", new RegisterRequest(uname, pw));
        reg.StatusCode.Should().Be(HttpStatusCode.Created);
        var session = await reg.Content.ReadFromJsonAsync<AuthSession>(SnakeCaseJson);

        var login = await client.PostAsJsonAsync("/api/auth/login", new LoginRequest(uname, pw));
        login.StatusCode.Should().Be(HttpStatusCode.OK);
        var loginSession = await login.Content.ReadFromJsonAsync<AuthSession>(SnakeCaseJson);
        loginSession!.Token.Should().NotBeNullOrEmpty();

        using var authed = _factory.CreateClient();
        authed.DefaultRequestHeaders.Authorization =
            new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", loginSession.Token);
        var me = await authed.GetAsync("/api/auth/me");
        me.StatusCode.Should().Be(HttpStatusCode.OK);
        var meBody = await me.Content.ReadFromJsonAsync<UserDto>(SnakeCaseJson);
        meBody!.Username.Should().Be(uname);
        meBody.UpdatedAt.Should().NotBe(default);
    }

    [Fact]
    public async Task Register_RejectsShortPassword()
    {
        using var client = _factory.CreateClient();
        var resp = await client.PostAsJsonAsync("/api/auth/register",
            new RegisterRequest("short_" + Guid.NewGuid().ToString("N")[..6], "abc"));
        resp.StatusCode.Should().Be(HttpStatusCode.UnprocessableEntity);
    }

    [Fact]
    public async Task Login_WithWrongPassword_Returns422()
    {
        using var client = _factory.CreateClient();
        var uname = "wp_" + Guid.NewGuid().ToString("N")[..8];
        var reg = await client.PostAsJsonAsync("/api/auth/register", new RegisterRequest(uname, "TestPass1234"));
        reg.StatusCode.Should().Be(HttpStatusCode.Created);

        var resp = await client.PostAsJsonAsync("/api/auth/login", new LoginRequest(uname, "WRONG"));
        resp.StatusCode.Should().Be(HttpStatusCode.UnprocessableEntity);
    }

    [Fact]
    public async Task Me_WithoutToken_Returns401()
    {
        using var client = _factory.CreateClient();
        var resp = await client.GetAsync("/api/auth/me");
        resp.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
    }

    [Fact]
    public async Task Register_PersistsUserRow_WithUpdatedAtPopulated()
    {
        // Regression guard for Bug 搂4.5: UserConfiguration must not produce
        // a schema where INSERTs to `users` need an explicit `updated_at`.
        // This test reaches past the API to confirm the row landed in the
        // shared SQLite and that updated_at has a DB-side default value.
        using var client = _factory.CreateClient();
        var uname = "row_" + Guid.NewGuid().ToString("N")[..8];
        var resp = await client.PostAsJsonAsync("/api/auth/register",
            new RegisterRequest(uname, "TestPass1234"));
        resp.StatusCode.Should().Be(HttpStatusCode.Created);

        using var scope = _factory.Services.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var user = await db.Users.AsNoTracking().FirstOrDefaultAsync(u => u.Username == uname);
        user.Should().NotBeNull();
        user!.UpdatedAt.Should().NotBe(default);
    }
}
