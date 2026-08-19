// SPDX-License-Identifier: MIT
namespace AgentBoard.Api.Api.Common;

/// <summary>
/// Uniform error envelope for every non-2xx response. Mirrors FastAPI's
/// <c>{"detail": "..."}</c> shape so the contract-freeze test (S0-5) passes
/// for every endpoint without per-handler translation.
/// </summary>
public sealed record ApiError(string Detail);
