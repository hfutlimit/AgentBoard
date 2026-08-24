// SPDX-License-Identifier: MIT
using System.Security.Cryptography;
using System.Text;

namespace AgentBoard.Application.Identity;

/// <summary>
/// Issues and validates the same stateless bearer token the FastAPI backend
/// uses: <c>v1.{user_id}.{expires_at}.{hmac_sig}</c>, where
/// <c>hmac_sig = HMAC-SHA256(secret, "v1.{uid}.{exp}")</c>. Mirroring the
/// format keeps a token issued by either stack acceptable to the other.
/// </summary>
public sealed class HmacTokenService : ITokenService
{
    private readonly byte[] _secret;
    private readonly int _ttlSeconds;

    public HmacTokenService(string secret, int ttlSeconds)
    {
        if (string.IsNullOrWhiteSpace(secret))
            throw new ArgumentException("token secret is required", nameof(secret));
        if (ttlSeconds <= 0)
            throw new ArgumentOutOfRangeException(nameof(ttlSeconds), "ttl must be positive");
        _secret = Encoding.UTF8.GetBytes(secret);
        _ttlSeconds = ttlSeconds;
    }

    public string IssueToken(int userId)
    {
        var expiresAt = DateTimeOffset.UtcNow.AddSeconds(_ttlSeconds).ToUnixTimeSeconds();
        var payload = $"v1.{userId}.{expiresAt}";
        return $"{payload}.{Sign(payload)}";
    }

    public int? ValidateToken(string? token)
    {
        if (string.IsNullOrWhiteSpace(token))
            return null;
        var parts = token.Split('.');
        if (parts.Length != 4 || parts[0] != "v1")
            return null;
        if (!int.TryParse(parts[1], out var uid) || !long.TryParse(parts[2], out var exp))
            return null;
        if (exp <= DateTimeOffset.UtcNow.ToUnixTimeSeconds())
            return null;
        var payload = $"v1.{parts[1]}.{parts[2]}";
        if (!FixedTimeEquals(Sign(payload), parts[3]))
            return null;
        return uid;
    }

    private string Sign(string payload)
    {
        using var hmac = new HMACSHA256(_secret);
        return Convert.ToHexString(hmac.ComputeHash(Encoding.UTF8.GetBytes(payload))).ToLowerInvariant();
    }

    private static bool FixedTimeEquals(string expected, string actual)
    {
        var a = Encoding.UTF8.GetBytes(expected);
        var b = Encoding.UTF8.GetBytes(actual);
        return CryptographicOperations.FixedTimeEquals(a, b);
    }
}
