// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Identity.Dtos;

/// <summary>Login payload — matches the FastAPI <c>LoginIn</c> shape.</summary>
public sealed record LoginRequest(string Username, string Password);
