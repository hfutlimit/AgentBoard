// SPDX-License-Identifier: MIT
using System.Security.Cryptography;
using AgentBoard.Domain.Common;

namespace AgentBoard.Application.Identity;

/// <summary>
/// Verifies and produces PBKDF2-SHA256 password hashes in the exact format
/// used by the FastAPI backend: <c>pbkdf2_sha256$ROUNDS$SALT$HEX</c>
/// (legacy 3-part <c>pbkdf2_sha256$SALT$HEX</c> with 100k rounds is also
/// accepted). A <c>plain:</c> prefix is tolerated for stage-0 dev accounts
/// created before hashing landed, so those credentials still validate.
/// </summary>
public sealed class Pbkdf2PasswordHasher : IPasswordHasher
{
    private const int Rounds = 600_000;
    private const int LegacyRounds = 100_000;
    private const int SaltBytes = 16;
    private const int DerivedBytes = 32;

    public bool Verify(string storedHash, string password)
    {
        if (string.IsNullOrWhiteSpace(storedHash) || string.IsNullOrWhiteSpace(password))
            return false;

        if (storedHash.StartsWith("plain:", StringComparison.Ordinal))
            return storedHash.Substring("plain:".Length) == password;

        var parts = storedHash.Split('$');
        if (parts.Length is not (3 or 4))
            return false;

        int rounds;
        string saltHex, expected;
        if (parts.Length == 3)
        {
            if (parts[0] != "pbkdf2_sha256") return false;
            rounds = LegacyRounds;
            saltHex = parts[1];
            expected = parts[2];
        }
        else
        {
            if (parts[0] != "pbkdf2_sha256") return false;
            if (!int.TryParse(parts[1], out rounds)) return false;
            saltHex = parts[2];
            expected = parts[3];
        }

        if (rounds <= 0 || rounds > 10_000_000)
            return false;
        if (!TryHexToBytes(saltHex, out var salt))
            return false;

        var dk = Rfc2898DeriveBytes.Pbkdf2(
            System.Text.Encoding.UTF8.GetBytes(password), salt, rounds, HashAlgorithmName.SHA256, DerivedBytes);

        return FixedTimeEquals(Convert.ToHexString(dk).ToLowerInvariant(), expected);
    }

    public string Hash(string password)
    {
        if (string.IsNullOrWhiteSpace(password))
            throw new InvalidValueException("password is required");

        var salt = RandomNumberGenerator.GetBytes(SaltBytes);
        var dk = Rfc2898DeriveBytes.Pbkdf2(
            System.Text.Encoding.UTF8.GetBytes(password), salt, Rounds, HashAlgorithmName.SHA256, DerivedBytes);

        return $"pbkdf2_sha256${Rounds}${Convert.ToHexString(salt).ToLowerInvariant()}${Convert.ToHexString(dk).ToLowerInvariant()}";
    }

    private static bool TryHexToBytes(string hex, out byte[] bytes)
    {
        try
        {
            bytes = Convert.FromHexString(hex);
            return true;
        }
        catch (FormatException)
        {
            bytes = Array.Empty<byte>();
            return false;
        }
    }

    private static bool FixedTimeEquals(string a, string b)
    {
        if (a.Length != b.Length)
            return false;
        var xa = System.Text.Encoding.UTF8.GetBytes(a);
        var xb = System.Text.Encoding.UTF8.GetBytes(b);
        return CryptographicOperations.FixedTimeEquals(xa, xb);
    }
}
