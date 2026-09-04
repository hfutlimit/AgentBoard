// SPDX-License-Identifier: MIT
using AgentBoard.Contracts;
using AgentBoard.Domain.Entities;
using AgentBoard.Domain.Workflow.Durable;
using AgentBoard.Infrastructure.Persistence;
using AgentBoard.Infrastructure.Scheduling;
using FluentAssertions;
using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;

namespace AgentBoard.Infrastructure.Tests.Scheduling;

public sealed class DatabaseAgentSelectorTests
{
    [Fact]
    public async Task Selection_enforces_owner_project_presence_capability_and_self_review_exclusion()
    {
        await using var connection = new SqliteConnection("Data Source=:memory:");
        await connection.OpenAsync();
        var services = new ServiceCollection();
        services.AddDbContext<AppDbContext>(options =>
            options.UseSqlite(connection));
        await using var provider = services.BuildServiceProvider();
        var now = DateTime.UtcNow;

        await using (var scope = provider.CreateAsyncScope())
        {
            var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
            await db.Database.EnsureCreatedAsync();
            db.ProjectMembers.Add(new ProjectMember
            {
                ProjectId = 3,
                UserId = 7,
                Role = "owner",
                JoinedAt = now,
            });
            AddCandidate(db, "agent.wrong-owner", "worker-wrong", owner: 8, online: true, now);
            AddCandidate(db, "agent.offline", "worker-offline", owner: 7, online: false, now);
            AddCandidate(db, "agent.review", "worker-review", owner: 7, online: true, now);
            await db.SaveChangesAsync();
        }

        var selector = new DatabaseAgentSelector(
            provider.GetRequiredService<IServiceScopeFactory>());
        var request = new AgentSelectionRequest(
            "run-1",
            "stage-review",
            StageType.Review,
            3,
            7,
            new[] { new AgentCapabilityRequirement("review", 2) },
            new HashSet<string>(StringComparer.Ordinal));

        var selected = selector.Select(request);

        selected.Should().NotBeNull();
        selected!.AgentId.Should().Be("agent.review");
        selected.WorkerId.Should().Be("worker-review");

        selector.Select(request with
        {
            ExcludedAgentIds = new HashSet<string>(new[] { "agent.review" }, StringComparer.Ordinal),
        }).Should().BeNull("an Agent may not review its own development work");
    }

    [Fact]
    public void Capability_contracts_parse_levels_and_reject_malformed_shapes()
    {
        var requirements = AgentCapabilityJson.ParseRequirements(
            "[\"development\",{\"name\":\"review\",\"minimum_level\":4}]");

        requirements.Should().ContainEquivalentOf(new AgentCapabilityRequirement("development", 1));
        requirements.Should().ContainEquivalentOf(new AgentCapabilityRequirement("review", 4));
        FluentActions.Invoking(() => AgentCapabilityJson.ParseRequirements("{}"))
            .Should().Throw<AgentBoard.Domain.Common.InvalidValueException>();
    }

    private static void AddCandidate(
        AppDbContext db,
        string agentId,
        string workerId,
        int owner,
        bool online,
        DateTime now)
    {
        db.Agents.Add(new Agent
        {
            AgentId = agentId,
            Name = agentId,
            Roles = "[]",
            Capabilities = "[{\"name\":\"review\",\"level\":3}]",
            UserId = owner,
            Online = online,
            Enabled = true,
            LastHeartbeat = now,
            CreatedAt = now,
            UpdatedAt = now,
        });
        db.Workers.Add(new Worker
        {
            WorkerId = workerId,
            Hostname = workerId,
            Status = "active",
            LastHeartbeat = now,
            CreatedAt = now,
            UpdatedAt = now,
        });
        db.AgentInstances.Add(new AgentInstance
        {
            WorkerId = workerId,
            AgentId = agentId,
            ExecutorType = "scenario",
            Enabled = true,
            Online = online,
            LastHeartbeat = now,
            CreatedAt = now,
            UpdatedAt = now,
        });
        db.WorkerProjectMappings.Add(new WorkerProjectMapping
        {
            WorkerId = workerId,
            ProjectId = 3,
            Enabled = true,
            CreatedAt = now,
        });
    }
}
