// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Entities;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata.Builders;

namespace AgentBoard.Infrastructure.Persistence.Configurations;

public sealed class WorkerConfiguration : ReadOnlyConfiguration<Worker>
{
    protected override void ConfigureEntity(EntityTypeBuilder<Worker> b)
    {
        b.ToTable("workers");
        b.Property(e => e.WorkerId).HasColumnName("worker_id").HasMaxLength(64).IsRequired();
        b.Property(e => e.Hostname).HasColumnName("hostname").HasMaxLength(200).HasDefaultValue(string.Empty);
        b.Property(e => e.Status).HasColumnName("status").HasMaxLength(20).HasDefaultValue("active");
        b.Property(e => e.LastHeartbeat).HasColumnName("last_heartbeat");
        b.Property(e => e.CreatedAt).HasColumnName("created_at");
        b.Property(e => e.UpdatedAt).HasColumnName("updated_at");
        b.HasIndex(e => e.WorkerId).IsUnique();
        b.HasIndex(e => e.Status);
    }
}

public sealed class AgentInstanceConfiguration : ReadOnlyConfiguration<AgentInstance>
{
    protected override void ConfigureEntity(EntityTypeBuilder<AgentInstance> b)
    {
        b.ToTable("agent_instances");
        b.Property(e => e.WorkerId).HasColumnName("worker_id").HasMaxLength(64).IsRequired();
        b.Property(e => e.AgentId).HasColumnName("agent_id").HasMaxLength(64).IsRequired();
        b.Property(e => e.CliCommand).HasColumnName("cli_command").HasMaxLength(500).HasDefaultValue(string.Empty);
        b.Property(e => e.Model).HasColumnName("model").HasMaxLength(100).HasDefaultValue(string.Empty);
        b.Property(e => e.ExecutorType).HasColumnName("executor_type").HasMaxLength(40);
        b.Property(e => e.AuthKey).HasColumnName("auth_key").HasMaxLength(100).HasDefaultValue(string.Empty);
        b.Property(e => e.Enabled).HasColumnName("enabled").HasDefaultValue(true);
        b.Property(e => e.Online).HasColumnName("online").HasDefaultValue(false);
        b.Property(e => e.LastHeartbeat).HasColumnName("last_heartbeat");
        b.Property(e => e.LastProbeAt).HasColumnName("last_probe_at");
        b.Property(e => e.ProbeMessage).HasColumnName("probe_message").HasMaxLength(300).HasDefaultValue(string.Empty);
        b.Property(e => e.CreatedAt).HasColumnName("created_at");
        b.Property(e => e.UpdatedAt).HasColumnName("updated_at");
        b.HasIndex(e => new { e.WorkerId, e.AgentId }).IsUnique();
        b.HasIndex(e => e.WorkerId);
        b.HasIndex(e => e.AgentId);
        b.HasIndex(e => e.ExecutorType);
        b.HasIndex(e => e.Online);
    }
}

public sealed class WorkerProjectMappingConfiguration : ReadOnlyConfiguration<WorkerProjectMapping>
{
    protected override void ConfigureEntity(EntityTypeBuilder<WorkerProjectMapping> b)
    {
        b.ToTable("worker_project_mappings");
        b.Property(e => e.WorkerId).HasColumnName("worker_id").HasMaxLength(64).IsRequired();
        b.Property(e => e.ProjectId).HasColumnName("project_id").IsRequired();
        b.Property(e => e.Enabled).HasColumnName("enabled").HasDefaultValue(true);
        b.Property(e => e.CreatedAt).HasColumnName("created_at");
        b.HasIndex(e => new { e.WorkerId, e.ProjectId }).IsUnique();
        b.HasIndex(e => e.WorkerId);
        b.HasIndex(e => e.ProjectId);
    }
}
