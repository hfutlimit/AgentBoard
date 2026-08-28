// SPDX-License-Identifier: MIT
using System.Linq.Expressions;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board.Dtos;
using AgentBoard.Domain.Common;
using AgentBoard.Domain.Entities;

namespace AgentBoard.Application.Board;

/// <summary>Webhook configuration. Mirrors FastAPI webhooks router.</summary>
public sealed class WebhookProvider : IWebhookProvider
{
    private readonly IWebhookConfigRepository _webhooks;
    private readonly IUnitOfWork _uow;

    public WebhookProvider(IWebhookConfigRepository webhooks, IUnitOfWork uow)
    {
        _webhooks = webhooks ?? throw new ArgumentNullException(nameof(webhooks));
        _uow = uow ?? throw new ArgumentNullException(nameof(uow));
    }

    public async Task<IReadOnlyList<WebhookDto>> ListWebhooksAsync(int? projectId, CancellationToken ct = default)
    {
        var items = projectId.HasValue
            ? await _webhooks.ListAsync(w => w.ProjectId == projectId, ct)
            : await _webhooks.ListAsync(ct: ct);
        return items.Select(ToDto).ToList();
    }

    public async Task<WebhookDto> CreateWebhookAsync(CreateWebhookRequest request, int? userId, CancellationToken ct = default)
    {
        var projectId = request.ProjectId;
        var name = (request.Name ?? string.Empty).Trim();
        if (name.Length == 0 || name.Length > 200)
            throw new InvalidValueException("name must be 1-200 characters");
        var url = (request.Url ?? string.Empty).Trim();
        if (url.Length == 0)
            throw new InvalidValueException("url is required");
        var events = request.Events;

        var webhook = new WebhookConfig
        {
            ProjectId = projectId,
            Name = name,
            Url = url,
            Events = events ?? "[]",
            Enabled = true,
            CreatedBy = userId,
            CreatedAt = DateTime.UtcNow,
            UpdatedAt = DateTime.UtcNow,
        };

        await _webhooks.AddAsync(webhook, ct);
        await _uow.SaveChangesAsync(ct);
        return ToDto(webhook);
    }

    public async Task<WebhookDto?> UpdateWebhookAsync(int id, UpdateWebhookRequest request, CancellationToken ct = default)
    {
        var webhook = await _webhooks.GetByIdAsync(id, ct);
        if (webhook is null) return null;

        var name = request.Name;
        var url = request.Url;
        var events = request.Events;
        var enabled = request.Enabled;
        if (name is not null) webhook.Name = name;
        if (url is not null) webhook.Url = url;
        if (events is not null) webhook.Events = events;
        if (enabled.HasValue) webhook.Enabled = enabled.Value;
        webhook.UpdatedAt = DateTime.UtcNow;

        _webhooks.Update(webhook);
        await _uow.SaveChangesAsync(ct);
        return ToDto(webhook);
    }

    public async Task<bool> DeleteWebhookAsync(int id, CancellationToken ct = default)
    {
        var webhook = await _webhooks.GetByIdAsync(id, ct);
        if (webhook is null) return false;
        _webhooks.Remove(webhook);
        await _uow.SaveChangesAsync(ct);
        return true;
    }

    private static WebhookDto ToDto(WebhookConfig w) =>
        new(w.Id, w.ProjectId, w.Name, w.Url, w.Events, w.Enabled, w.CreatedAt);
}
