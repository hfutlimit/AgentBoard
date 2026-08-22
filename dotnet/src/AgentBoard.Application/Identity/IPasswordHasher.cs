// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Identity;

/// <summary>
/// Verifies and produces password hashes compatible with the FastAPI
/// backend, so the .NET BFF can authenticate against the same
/// <c>users</c> table without re-hashing.
/// </summary>
public interface IPasswordHasher
{
    /// <summary>Returns true if <paramref name="password"/> matches the
    /// stored hash.</summary>
    bool Verify(string storedHash, string password);

    /// <summary>Produces a new hash for <paramref name="password"/>.</summary>
    string Hash(string password);
}
