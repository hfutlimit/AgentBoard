// SPDX-License-Identifier: MIT
namespace AgentBoard.Application.Abstractions;

/// <summary>
/// Time provider. Wrapping <see cref="DateTime.UtcNow"/> behind an interface
/// keeps entity creation deterministic under unit tests.
/// </summary>
public interface IClock
{
    DateTime UtcNow { get; }
}
