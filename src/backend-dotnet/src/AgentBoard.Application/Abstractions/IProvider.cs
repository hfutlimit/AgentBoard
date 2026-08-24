// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Abstractions;

/// <summary>
/// Marker interface for application Providers. Providers compose multiple
/// Services to deliver a single API endpoint's worth of behaviour, including
/// cross-cutting concerns (caching, transactions, event publication).
/// </summary>
public interface IProvider;
