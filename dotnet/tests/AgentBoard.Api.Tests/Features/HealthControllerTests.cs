// SPDX-License-Identifier: MIT
using System.Net;
using System.Net.Http.Json;
using AgentBoard.Api.Features.Health;
using FluentAssertions;
using Microsoft.AspNetCore.Mvc.Testing;

namespace AgentBoard.Api.Tests.Features;

public sealed class HealthControllerTests : IClassFixture<WebApplicationFactory<Program>>
{
    private readonly WebApplicationFactory<Program> _factory;

    public HealthControllerTests(WebApplicationFactory<Program> factory)
    {
        _factory = factory;
    }

    [Fact]
    public async Task Get_Returns_200_And_Shape_That_Matches_FastAPI()
    {
        using var client = _factory.CreateClient();
        var response = await client.GetAsync("/api/health");

        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var dto = await response.Content.ReadFromJsonAsync<HealthResponseDto>();
        dto.Should().NotBeNull();
        dto!.Status.Should().Be("ok");
        dto.Database.Should().Be("ok");
        dto.Version.Should().NotBeNullOrEmpty();
        dto.Timestamp.Kind.Should().Be(DateTimeKind.Utc);
    }

    [Fact]
    public async Task Get_DoesNot_Require_Authentication()
    {
        using var client = _factory.CreateClient();
        var response = await client.GetAsync("/api/health");
        response.StatusCode.Should().Be(HttpStatusCode.OK);
    }
}
