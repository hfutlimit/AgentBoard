// SPDX-License-Identifier: MIT
using System.Net;
using System.Net.Http.Json;
using AgentBoard.Api.Tests.Infrastructure;
using AgentBoard.Api.Features.Meta.Dtos;
using FluentAssertions;

namespace AgentBoard.Api.Tests.Features;

public sealed class MetaControllerTests : IClassFixture<ApiWebApplicationFactory>
{
    private readonly ApiWebApplicationFactory _factory;

    public MetaControllerTests(ApiWebApplicationFactory factory)
    {
        _factory = factory;
    }

    [Fact]
    public async Task Get_Returns_200_And_All_Expected_Enum_Collections()
    {
        using var client = _factory.CreateClient();
        var response = await client.GetAsync("/api/meta");

        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var dto = await response.Content.ReadFromJsonAsync<MetaResponseDto>();
        dto.Should().NotBeNull();

        // Mirrors FastAPI's `core/common/enums.py` exactly. Any drift here
        // is a contract violation that the OpenAPI snapshot regen will
        // surface as a CI failure.
        dto!.Types.Should().Equal(new[] { "dev", "bug", "qa", "design" });
        dto.Statuses.Should().Equal(new[] { "todo", "in_progress", "in_review", "done", "blocked" });
        dto.Priorities.Should().Equal(new[] { "highest", "high", "medium", "low", "lowest" });
        dto.SprintStatuses.Should().Equal(new[] { "planning", "active", "completed" });
        dto.ScheduleTypes.Should().Equal(new[] { "once", "cron" });
        dto.RunStatuses.Should().Equal(new[] { "pending", "running", "success", "failed", "cancelled" });
    }

    [Fact]
    public async Task Get_DoesNot_Require_Authentication()
    {
        // FastAPI skips require_business_auth for /api/meta — the .NET side
        // must match (otherwise the contract-freeze test will fail once
        // the live drift check is enabled).
        using var client = _factory.CreateClient();
        var response = await client.GetAsync("/api/meta");
        response.StatusCode.Should().Be(HttpStatusCode.OK);
    }

    [Fact]
    public async Task Get_Wire_Format_Uses_Snake_Case_Keys()
    {
        // FastAPI / Pydantic emit field names verbatim (snake_case). The
        // .NET DTO locks the wire format with [JsonPropertyName] so a
        // future ASP.NET Core serializer change can't silently break
        // the contract-freeze test.
        using var client = _factory.CreateClient();
        var response = await client.GetAsync("/api/meta");
        var raw = await response.Content.ReadAsStringAsync();

        raw.Should().Contain("\"types\"");
        raw.Should().Contain("\"statuses\"");
        raw.Should().Contain("\"priorities\"");
        raw.Should().Contain("\"sprint_statuses\"");
        raw.Should().Contain("\"schedule_types\"");
        raw.Should().Contain("\"run_statuses\"");
        raw.Should().NotContain("\"sprintStatuses\"");
        raw.Should().NotContain("\"scheduleTypes\"");
        raw.Should().NotContain("\"runStatuses\"");
    }
}
