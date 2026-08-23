// SPDX-License-Identifier: MIT
using System.Net;
using System.Net.Http.Json;
using AgentBoard.Api.Tests.Infrastructure;
using AgentBoard.Domain.Entities;
using AgentBoard.Infrastructure.Persistence;
using FluentAssertions;
using Microsoft.Extensions.DependencyInjection;

namespace AgentBoard.Api.Tests.Features;

public sealed class DocumentFoldersControllerTests : IClassFixture<ApiWebApplicationFactory>
{
    private readonly ApiWebApplicationFactory _factory;

    public DocumentFoldersControllerTests(ApiWebApplicationFactory factory) => _factory = factory;

    private async Task<int> SeedProjectAsync(string name)
    {
        using var scope = _factory.Services.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var p = new Project { Name = name, Key = name.ToLowerInvariant(), CreatedAt = DateTime.UtcNow };
        db.Projects.Add(p);
        await db.SaveChangesAsync();
        return p.Id;
    }

    [Fact]
    public async Task List_Returns_400_When_ProjectId_Missing()
    {
        using var client = _factory.CreateClient();
        var response = await client.GetAsync("/api/document-folders");
        response.StatusCode.Should().Be(HttpStatusCode.BadRequest);
    }

    [Fact]
    public async Task List_Returns_200_With_Empty_Items_For_Unknown_Project()
    {
        using var client = _factory.CreateClient();
        var response = await client.GetAsync("/api/document-folders?project_id=999999");
        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var body = await response.Content.ReadFromJsonAsync<FolderListResponse>();
        body.Should().NotBeNull();
        body!.items.Should().BeEmpty();
        body.total.Should().Be(0);
    }

    [Fact]
    public async Task Create_Returns_201_With_Body_For_Existing_Project()
    {
        var projectId = await SeedProjectAsync("folders-create-" + Guid.NewGuid().ToString("N")[..8]);
        using var client = _factory.CreateClient();
        var response = await client.PostAsJsonAsync("/api/document-folders",
            new { project_id = projectId, name = "plans" });
        response.StatusCode.Should().Be(HttpStatusCode.Created);
        var dto = await response.Content.ReadFromJsonAsync<FolderDto>();
        dto.Should().NotBeNull();
        dto!.project_id.Should().Be(projectId);
        dto.name.Should().Be("plans");
        dto.parent_id.Should().BeNull();
        dto.id.Should().BeGreaterThan(0);
    }

    [Fact]
    public async Task Create_Returns_400_When_ProjectId_Missing()
    {
        using var client = _factory.CreateClient();
        var response = await client.PostAsJsonAsync("/api/document-folders", new { name = "x" });
        response.StatusCode.Should().Be(HttpStatusCode.BadRequest);
    }

    [Fact]
    public async Task Delete_Returns_204_For_Known_Folder_Then_404()
    {
        var projectId = await SeedProjectAsync("folders-delete-" + Guid.NewGuid().ToString("N")[..8]);
        using var client = _factory.CreateClient();

        var create = await client.PostAsJsonAsync("/api/document-folders",
            new { project_id = projectId, name = "tmp" });
        create.StatusCode.Should().Be(HttpStatusCode.Created);
        var folder = await create.Content.ReadFromJsonAsync<FolderDto>();

        var del = await client.DeleteAsync($"/api/document-folders/{folder!.id}");
        del.StatusCode.Should().Be(HttpStatusCode.NoContent);

        var delAgain = await client.DeleteAsync($"/api/document-folders/{folder.id}");
        delAgain.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }

    [Fact]
    public async Task Delete_Returns_404_For_Unknown_Folder()
    {
        using var client = _factory.CreateClient();
        var response = await client.DeleteAsync("/api/document-folders/999999");
        response.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }

    private sealed record FolderListResponse(IReadOnlyList<FolderDto> items, int total);
    private sealed record FolderDto(int id, int project_id, int? parent_id, string name, DateTime created_at, DateTime updated_at);
}
