// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board.Dtos;

namespace AgentBoard.Application.Board;

/// <summary>API key management. Mirrors FastAPI api-keys router.</summary>
public interface IApiKeyProvider : IProvider
{
    Task<IReadOnlyList<ApiKeyDto>> ListApiKeysAsync(int userId, CancellationToken ct = default);
    Task<(ApiKeyDto Dto, string RawKey)> CreateApiKeyAsync(int userId, string? name, string? scopes, CancellationToken ct = default);
    Task<bool> DeleteApiKeyAsync(int keyId, int userId, CancellationToken ct = default);
}
