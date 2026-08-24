// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board.Dtos;

namespace AgentBoard.Application.Board;

/// <summary>Webhook configuration. Mirrors FastAPI webhooks router.</summary>
public interface IWebhookProvider : IProvider
{
    Task<IReadOnlyList<WebhookDto>> ListWebhooksAsync(int? projectId, CancellationToken ct = default);
    Task<WebhookDto> CreateWebhookAsync(int? projectId, string? name, string? url, string? events, int? userId, CancellationToken ct = default);
    Task<WebhookDto?> UpdateWebhookAsync(int id, string? name, string? url, string? events, bool? enabled, CancellationToken ct = default);
    Task<bool> DeleteWebhookAsync(int id, CancellationToken ct = default);
}
