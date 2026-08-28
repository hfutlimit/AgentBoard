// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Board.Dtos;

/// <summary>Request body for <c>POST /api/webhooks</c>. ProjectId optional (null = global webhook).</summary>
public sealed record CreateWebhookRequest(
    int? ProjectId,
    string? Name,
    string? Url,
    string? Events);

/// <summary>Request body for <c>PATCH /api/webhooks/{id}</c>. All fields optional; null = leave unchanged.</summary>
public sealed record UpdateWebhookRequest(
    string? Name,
    string? Url,
    string? Events,
    bool? Enabled);
