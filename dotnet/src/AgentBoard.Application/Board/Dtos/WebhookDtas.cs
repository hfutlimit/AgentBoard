// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Board.Dtos;

/// <summary>Webhook configuration record. Mirrors FastAPI <c>WebhookOut</c>.</summary>
public sealed record WebhookDto(
    int Id,
    int? ProjectId,
    string Name,
    string Url,
    string Events,
    bool Enabled,
    DateTime CreatedAt);

/// <summary>Request body for <c>POST /api/webhooks</c>.</summary>
public sealed record WebhookCreateRequest(
    string? Name,
    string? Url,
    string? Events,
    int? ProjectId);

/// <summary>Request body for <c>PATCH /api/webhooks/{id}</c>.</summary>
public sealed record WebhookPatchRequest(
    string? Name,
    string? Url,
    string? Events,
    bool? Enabled);
