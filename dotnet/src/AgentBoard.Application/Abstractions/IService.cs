// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Abstractions;

/// <summary>
/// Marker interface for application Services. Lets us filter the DI container
/// (or unit-of-work registrations) by Service vs. Provider, and enables
/// NetArchTest rules that forbid direct Controller → Service calls.
/// </summary>
public interface IService;
