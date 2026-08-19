// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;

namespace AgentBoard.Infrastructure.Time;

/// <summary>Default <see cref="IClock"/> implementation — wraps <see cref="DateTime.UtcNow"/>.</summary>
public sealed class SystemClock : IClock
{
    public DateTime UtcNow => DateTime.UtcNow;
}
