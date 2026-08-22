// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Board.Dtos;

/// <summary>API key record (secret never exposed). Mirrors FastAPI <c>ApiKeyOut</c>.</summary>
public sealed record ApiKeyDto(
    int Id,
    string Name,
    string KeyPrefix,
    string Scopes,
    bool Enabled,
    DateTime? LastUsedAt,
    DateTime CreatedAt);

/// <summary>Request body for <c>POST /api/api-keys</c>.</summary>
public sealed record ApiKeyCreateRequest(
    string? Name,
    string? Scopes);

/// <summary>Response when creating an API key (includes the raw secret once).</summary>
public sealed record ApiKeyCreatedResponse(
    int Id,
    string Name,
    string KeyPrefix,
    string Scopes,
    bool Enabled,
    DateTime CreatedAt,
    string RawKey);
