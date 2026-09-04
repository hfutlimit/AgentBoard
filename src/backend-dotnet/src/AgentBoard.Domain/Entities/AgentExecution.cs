// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Entities;

/// <summary>Physical Worker identity mirrored from the FastAPI-owned workers table.</summary>
public sealed class Worker : Entity
{
    public string WorkerId { get; set; } = string.Empty;
    public string Hostname { get; set; } = string.Empty;
    public string Status { get; set; } = "active";
    public DateTime? LastHeartbeat { get; set; }
    public DateTime CreatedAt { get; set; }
    public DateTime UpdatedAt { get; set; }
}

/// <summary>Runnable binding of one logical Agent to one physical Worker.</summary>
public sealed class AgentInstance : Entity
{
    public string WorkerId { get; set; } = string.Empty;
    public string AgentId { get; set; } = string.Empty;
    public string CliCommand { get; set; } = string.Empty;
    public string Model { get; set; } = string.Empty;
    public string? ExecutorType { get; set; }
    public string AuthKey { get; set; } = string.Empty;
    public bool Enabled { get; set; } = true;
    public bool Online { get; set; }
    public DateTime? LastHeartbeat { get; set; }
    public DateTime? LastProbeAt { get; set; }
    public string ProbeMessage { get; set; } = string.Empty;
    public DateTime CreatedAt { get; set; }
    public DateTime UpdatedAt { get; set; }
}

/// <summary>Project authorization for a Worker; user ownership remains on Agent.</summary>
public sealed class WorkerProjectMapping : Entity
{
    public string WorkerId { get; set; } = string.Empty;
    public int ProjectId { get; set; }
    public bool Enabled { get; set; } = true;
    public DateTime CreatedAt { get; set; }
}
