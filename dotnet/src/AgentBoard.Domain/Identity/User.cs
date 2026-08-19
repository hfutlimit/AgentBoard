// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Identity;

/// <summary>
/// A registered AgentBoard user. The password is stored as a PBKDF2 hash
/// (format-versioned to support future algorithm upgrades); the raw value
/// is never held on the entity.
/// </summary>
public sealed class User : Entity, IAuditableEntity
{
    public string Username { get; private set; } = string.Empty;
    public string PasswordHash { get; private set; } = string.Empty;
    public string? DisplayName { get; private set; }
    public string? Email { get; private set; }
    public string? AvatarUrl { get; private set; }
    public bool IsAdmin { get; private set; }

    public DateTime CreatedAt { get; private set; }
    public DateTime UpdatedAt { get; private set; }
    public int? CreatedBy { get; private set; }
    public int? UpdatedBy { get; private set; }

    // EF Core parameterless ctor.
    private User() { }

    private User(string username, string passwordHash, bool isAdmin, DateTime now)
    {
        Username = username;
        PasswordHash = passwordHash;
        IsAdmin = isAdmin;
        CreatedAt = now;
        UpdatedAt = now;
    }

    public static User Create(string username, string passwordHash, bool isAdmin, DateTime now, int? createdBy = null)
    {
        if (string.IsNullOrWhiteSpace(username))
            throw new InvalidValueException("username is required");
        if (string.IsNullOrWhiteSpace(passwordHash))
            throw new InvalidValueException("passwordHash is required");
        var user = new User(username, passwordHash, isAdmin, now)
        {
            CreatedBy = createdBy,
        };
        user.RaiseDomainEvent(new UserCreatedEvent(user.Username, now));
        return user;
    }

    public void UpdateProfile(string? displayName, string? email, string? avatarUrl, DateTime now, int updatedBy)
    {
        DisplayName = displayName;
        Email = email;
        AvatarUrl = avatarUrl;
        UpdatedAt = now;
        UpdatedBy = updatedBy;
    }

    public void ChangePassword(string newPasswordHash, DateTime now, int updatedBy)
    {
        PasswordHash = newPasswordHash;
        UpdatedAt = now;
        UpdatedBy = updatedBy;
        RaiseDomainEvent(new UserPasswordChangedEvent(Id, now));
    }

    public void PromoteToAdmin(DateTime now, int updatedBy)
    {
        if (IsAdmin) return;
        IsAdmin = true;
        UpdatedAt = now;
        UpdatedBy = updatedBy;
    }
}

/// <summary>Raised when a new user record is created.</summary>
public sealed record UserCreatedEvent(string Username, DateTime OccurredAt) : IDomainEvent;

/// <summary>Raised after a successful password change.</summary>
public sealed record UserPasswordChangedEvent(int UserId, DateTime OccurredAt) : IDomainEvent;
