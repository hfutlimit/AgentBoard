// SPDX-License-Identifier: MIT
using System.Net;
using AgentBoard.Api.Tests.Infrastructure;
using FluentAssertions;

namespace AgentBoard.Api.Tests.Features;

public sealed class AttachmentsControllerTests : IClassFixture<ApiWebApplicationFactory>
{
    private readonly ApiWebApplicationFactory _factory;

    public AttachmentsControllerTests(ApiWebApplicationFactory factory) => _factory = factory;

    [Fact]
    public async Task GetInfo_Returns_404_For_Unknown_Attachment()
    {
        using var client = _factory.CreateClient();
        var response = await client.GetAsync("/api/attachments/999999/info");
        response.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }
}
