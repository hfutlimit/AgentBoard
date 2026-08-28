// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Identity.Dtos;

/// <summary>Payload for <c>PATCH /api/auth/me</c>. All fields optional; null = leave unchanged.</summary>
public sealed record UpdateProfileRequest(
    string? DisplayName,
    string? Email,
    string? AvatarUrl);
