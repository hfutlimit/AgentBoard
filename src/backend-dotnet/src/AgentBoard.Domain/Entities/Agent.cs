// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Entities;

/// <summary>
/// Agent registry row (Epic 122 S1, FastAPI parity).
///
/// Mirrors the Python <c>agents</c> SQLAlchemy table (see
/// <c>agentboard/features/projects/models.py:Agent</c>):
///   <list type="bullet">
///     <item><c>agent_id</c> — external string identifier (e.g. <c>wb-dev-1</c>),
///           used as the URL key for <c>PUT/DELETE /api/agents/{agentId}</c> and
///           as the unique registration key.</item>
///     <item><c>name</c> — display name; required.</item>
///     <item><c>roles</c> / <c>capabilities</c> — JSON list strings (e.g.
///           <c>"[\"reviewer\",\"developer\"]"</c>); normalize to JSON list
///           in the Provider before persistence.</item>
///     <item><c>cli_command</c> / <c>model</c> — CLI template (supports
///           <c>{model}</c> placeholder) and bound model name. Same CLI may
///           back multiple agents with different models.</item>
///     <item><c>online</c> / <c>enabled</c> — runtime liveness vs. admin
///           enable flag. <c>enabled=false</c> hides the agent from
///           assignment pools.</item>
///     <item><c>last_heartbeat</c> / <c>last_probe_at</c> — diagnostics the
///           frontend surfaces in the Agent Pool card.</item>
///   </list>
///
/// The .NET BFF is read/write for the Stage 2 follow-up; the Python side
/// remains source-of-truth for production data.
/// </summary>
public sealed class Agent : Entity
{
    /// <summary>External string identifier (unique). URL key for /api/agents/{agentId}.</summary>
    public string AgentId { get; set; } = string.Empty;

    /// <summary>Display name. Required, 1-100 chars.</summary>
    public string Name { get; set; } = string.Empty;

    /// <summary>JSON array string of role tags (e.g. <c>"[\"reviewer\"]"</c>). Max 200 chars on the wire.</summary>
    public string Roles { get; set; } = "[]";

    /// <summary>JSON array string of capability tags. Stored as TEXT.</summary>
    public string Capabilities { get; set; } = "[]";

    /// <summary>CLI invocation template (supports <c>{model}</c> placeholder). Max 500 chars.</summary>
    public string CliCommand { get; set; } = string.Empty;

    /// <summary>Model name bound to this agent (e.g. <c>hy3</c>). Max 100 chars.</summary>
    public string Model { get; set; } = string.Empty;

    /// <summary>Auth key fingerprint (not the secret itself). Max 100 chars.</summary>
    public string AuthKey { get; set; } = string.Empty;

    /// <summary>Bound service-account user id (optional). When set, the AgentBoard user owns this agent.</summary>
    public int? UserId { get; set; }

    /// <summary>Runtime liveness — set true on heartbeat, false on probe failure / deregister.</summary>
    public bool Online { get; set; }

    /// <summary>Admin enable flag. When false, the agent is hidden from assignment pools.</summary>
    public bool Enabled { get; set; } = true;

    /// <summary>Last successful heartbeat timestamp (UTC). Null until the first heartbeat.</summary>
    public DateTime? LastHeartbeat { get; set; }

    /// <summary>Probe diagnostic (e.g. "OK v1.2.3" / "timeout 8s"). Max 300 chars.</summary>
    public string ProbeMessage { get; set; } = string.Empty;

    /// <summary>Last probe attempt timestamp (UTC). Null until the first probe.</summary>
    public DateTime? LastProbeAt { get; set; }

    public DateTime CreatedAt { get; set; }
    public DateTime UpdatedAt { get; set; }
}
