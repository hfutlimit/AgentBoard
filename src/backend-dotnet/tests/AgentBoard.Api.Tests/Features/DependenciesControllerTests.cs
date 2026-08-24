// SPDX-License-Identifier: MIT
using System.Net;
using AgentBoard.Api.Tests.Infrastructure;
using FluentAssertions;

namespace AgentBoard.Api.Tests.Features;

public sealed class DependenciesControllerTests : IClassFixture<ApiWebApplicationFactory>
{
    private readonly ApiWebApplicationFactory _factory;

    public DependenciesControllerTests(ApiWebApplicationFactory factory) => _factory = factory;

    [Fact]
    public async Task Delete_Returns_404_For_Unknown_Dependency()
    {
        using var client = _factory.CreateClient();
        var response = await client.DeleteAsync("/api/dependencies/999999");
        response.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }
}
