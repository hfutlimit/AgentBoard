// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Entities;

/// <summary>
/// Webhook configuration. Maps to the <c>webhook_configs</c> table.
/// </summary>
public sealed class WebhookConfig : Entity
{
    public int? ProjectId { get; set; }
    public string Name { get; set; } = string.Empty;
    public string Url { get; set; } = string.Empty;
    public string? Secret { get; set; }
    public string Events { get; set; } = "[]";
    public bool Enabled { get; set; } = true;
    public int? CreatedBy { get; set; }
    public DateTime CreatedAt { get; set; }
    public DateTime UpdatedAt { get; set; }
}
