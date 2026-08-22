// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Entities;

/// <summary>
/// API key credential. Maps to the <c>api_keys</c> table.
/// The plaintext secret is never persisted — only the HMAC digest.
/// </summary>
public sealed class ApiKey : Entity
{
    public int UserId { get; set; }
    public int? AgentRegistryId { get; set; }
    public string Name { get; set; } = string.Empty;
    public string KeyPrefix { get; set; } = string.Empty;
    public string KeyHash { get; set; } = string.Empty;
    public string Scopes { get; set; } = "[\"api:read\",\"api:write\"]";
    public bool Enabled { get; set; } = true;
    public DateTime? LastUsedAt { get; set; }
    public DateTime CreatedAt { get; set; }
}
