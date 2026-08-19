// SPDX-License-Identifier: MIT
using AgentBoard.Application.Health;
using AgentBoard.Application.Identity;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;

namespace AgentBoard.Application;

/// <summary>
/// Wires up Application-layer Services + Providers into the DI container.
/// Called from <c>Program.cs</c> via <c>builder.Services.AddApplication()</c>.
/// </summary>
public static class DependencyInjection
{
    public static IServiceCollection AddApplication(this IServiceCollection services)
    {
        ArgumentNullException.ThrowIfNull(services);

        // Services — one per aggregate, scoped (matches the underlying DbContext lifetime).
        services.TryAddScoped<IUserService, UserService>();
        services.TryAddScoped<IHealthService, HealthService>();

        // Providers — composed on top of Services; Controllers depend on these.
        services.TryAddScoped<IAuthProvider, AuthProvider>();
        services.TryAddScoped<IHealthProvider, HealthProvider>();

        return services;
    }
}
