// SPDX-License-Identifier: MIT
using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using AgentBoard.Application.Board.Dtos;
using AgentBoard.Application.Identity;
using AgentBoard.Api.Tests.Infrastructure;
using AgentBoard.Domain.Entities;
using AgentBoard.Domain.Identity;
using AgentBoard.Infrastructure.Persistence;
using FluentAssertions;
using Microsoft.Extensions.DependencyInjection;

namespace AgentBoard.Api.Tests.Features;

public sealed class AttachmentsControllerTests : IClassFixture<ApiWebApplicationFactory>
{
    private readonly ApiWebApplicationFactory _factory;

    public AttachmentsControllerTests(ApiWebApplicationFactory factory) => _factory = factory;

    private async Task<(int TaskId, int AttachmentId)> SeedAttachmentAsync()
    {
        await using var scope = _factory.Services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var project = new Project
        {
            Name = "Attachment contract project",
            Key = $"ATT-{Guid.NewGuid():N}"[..12],
            Description = string.Empty,
            CreatedAt = DateTime.UtcNow,
        };
        db.Projects.Add(project);
        await db.SaveChangesAsync();
        var task = new TaskItem
        {
            ProjectId = project.Id,
            Type = "dev",
            Title = "attachment contract task",
            Status = "todo",
            Priority = "medium",
            Description = string.Empty,
            Spec = string.Empty,
            Labels = "[]",
            NeededCapabilities = "[]",
            DomainTags = "[]",
            AssignmentMode = "claim",
            CreatedAt = DateTime.UtcNow,
            UpdatedAt = DateTime.UtcNow,
        };
        db.Tasks.Add(task);
        await db.SaveChangesAsync();
        var attachment = new Attachment
        {
            TaskId = task.Id,
            Filename = "stored-report.pdf",
            OriginalName = "report.pdf",
            Size = 128,
            MimeType = "application/pdf",
            CreatedAt = DateTime.UtcNow,
        };
        db.Attachments.Add(attachment);
        await db.SaveChangesAsync();
        return (task.Id, attachment.Id);
    }

    private HttpClient NewAdminClient()
    {
        using var scope = _factory.Services.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var user = db.Users.SingleOrDefault(u => u.Username == "attachments-test-admin");
        if (user is null)
        {
            user = User.Create("attachments-test-admin", "test-hash", isAdmin: true, DateTime.UtcNow);
            db.Users.Add(user);
            db.SaveChanges();
        }
        var tokenService = scope.ServiceProvider.GetRequiredService<ITokenService>();
        var client = _factory.CreateClient();
        client.DefaultRequestHeaders.Authorization =
            new AuthenticationHeaderValue("Bearer", tokenService.IssueToken(user.Id));
        return client;
    }

    [Fact]
    public async Task GetInfo_Returns_404_For_Unknown_Attachment()
    {
        using var client = _factory.CreateClient();
        var response = await client.GetAsync("/api/attachments/999999/info");
        response.StatusCode.Should().Be(HttpStatusCode.NotFound);
    }

    [Fact]
    public async Task ExistingAttachment_CanBeListedReadAndDeleted()
    {
        var (taskId, attachmentId) = await SeedAttachmentAsync();
        using var client = NewAdminClient();

        var list = await client.GetAsync($"/api/tasks/{taskId}/attachments");
        list.StatusCode.Should().Be(HttpStatusCode.OK);
        var items = await list.Content.ReadFromJsonAsync<List<AttachmentDto>>();
        items.Should().ContainSingle(item => item.Id == attachmentId);

        var info = await client.GetAsync($"/api/attachments/{attachmentId}/info");
        info.StatusCode.Should().Be(HttpStatusCode.OK);

        var deleted = await client.DeleteAsync($"/api/tasks/{taskId}/attachments/{attachmentId}");
        deleted.StatusCode.Should().Be(HttpStatusCode.NoContent);
    }
}
