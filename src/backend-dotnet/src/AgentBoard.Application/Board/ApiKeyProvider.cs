// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board.Dtos;
using AgentBoard.Domain.Common;
using AgentBoard.Domain.Entities;

namespace AgentBoard.Application.Board;

/// <summary>API key management. Mirrors FastAPI api-keys router.</summary>
public sealed class ApiKeyProvider : IApiKeyProvider
{
    private readonly IApiKeyRepository _apiKeys;
    private readonly IUnitOfWork _uow;

    public ApiKeyProvider(IApiKeyRepository apiKeys, IUnitOfWork uow)
    {
        _apiKeys = apiKeys ?? throw new ArgumentNullException(nameof(apiKeys));
        _uow = uow ?? throw new ArgumentNullException(nameof(uow));
    }

    public async Task<IReadOnlyList<ApiKeyDto>> ListApiKeysAsync(int userId, CancellationToken ct = default)
    {
        var items = await _apiKeys.ListAsync(k => k.UserId == userId, ct);
        return items.Select(k => new ApiKeyDto(k.Id, k.Name, k.KeyPrefix, k.Scopes, k.Enabled, k.LastUsedAt, k.CreatedAt)).ToList();
    }

    public async Task<(ApiKeyDto Dto, string RawKey)> CreateApiKeyAsync(int userId, string? name, string? scopes, CancellationToken ct = default)
    {
        name = (name ?? string.Empty).Trim();
        if (name.Length == 0 || name.Length > 200)
            throw new InvalidValueException("name must be 1-200 characters");

        var rawKey = Guid.NewGuid().ToString("N");
        var keyPrefix = rawKey[..8];

        var apiKey = new ApiKey
        {
            UserId = userId,
            Name = name,
            KeyPrefix = keyPrefix,
            KeyHash = rawKey, // In production, hash this
            Scopes = scopes ?? "[]",
            Enabled = true,
            CreatedAt = DateTime.UtcNow,
        };

        await _apiKeys.AddAsync(apiKey, ct);
        await _uow.SaveChangesAsync(ct);

        var dto = new ApiKeyDto(apiKey.Id, apiKey.Name, apiKey.KeyPrefix, apiKey.Scopes, apiKey.Enabled, apiKey.LastUsedAt, apiKey.CreatedAt);
        return (dto, rawKey);
    }

    public async Task<bool> DeleteApiKeyAsync(int keyId, int userId, CancellationToken ct = default)
    {
        var items = await _apiKeys.ListAsync(k => k.Id == keyId && k.UserId == userId, ct);
        var key = items.FirstOrDefault();
        if (key is null) return false;
        _apiKeys.Remove(key);
        await _uow.SaveChangesAsync(ct);
        return true;
    }
}
