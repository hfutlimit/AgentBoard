// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Entities;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata.Builders;

namespace AgentBoard.Infrastructure.Persistence.Configurations;

/// <summary>
/// EF Core mapping for <see cref="Agent"/>. Column names mirror the FastAPI
/// <c>agents</c> table (snake_case) so a single MariaDB instance can serve
/// both stacks — see <c>ReadOnlyConfigurations.cs</c> header for the
/// dual-stack schema ownership rules.
///
/// New file (deliberately not appended to <c>ReadOnlyConfigurations.cs</c>)
/// to keep Stage 2 module 2's diff easy to review against the root
/// session's merge.
/// </summary>
public sealed class AgentConfiguration : ReadOnlyConfiguration<Agent>
{
    protected override void ConfigureEntity(EntityTypeBuilder<Agent> b)
    {
        b.ToTable("agents");
        b.Property(e => e.AgentId).HasColumnName("agent_id").HasMaxLength(64).IsRequired();
        b.HasIndex(e => e.AgentId).IsUnique();
        b.Property(e => e.Name).HasColumnName("name").HasMaxLength(100).IsRequired();
        b.Property(e => e.Roles).HasColumnName("roles").HasMaxLength(200).HasDefaultValue("[]");
        b.Property(e => e.Capabilities).HasColumnName("capabilities").HasDefaultValue("[]");
        b.Property(e => e.CliCommand).HasColumnName("cli_command").HasMaxLength(500).HasDefaultValue(string.Empty);
        b.Property(e => e.Model).HasColumnName("model").HasMaxLength(100).HasDefaultValue(string.Empty);
        b.Property(e => e.AuthKey).HasColumnName("auth_key").HasMaxLength(100).HasDefaultValue(string.Empty);
        b.Property(e => e.UserId).HasColumnName("user_id");
        b.Property(e => e.Online).HasColumnName("online").HasDefaultValue(false);
        b.Property(e => e.Enabled).HasColumnName("enabled").HasDefaultValue(true);
        b.Property(e => e.LastHeartbeat).HasColumnName("last_heartbeat");
        b.Property(e => e.ProbeMessage).HasColumnName("probe_message").HasMaxLength(300).HasDefaultValue(string.Empty);
        b.Property(e => e.LastProbeAt).HasColumnName("last_probe_at");
        b.Property(e => e.CreatedAt).HasColumnName("created_at");
        b.Property(e => e.UpdatedAt).HasColumnName("updated_at");
        b.HasIndex(e => e.UserId);
    }
}
