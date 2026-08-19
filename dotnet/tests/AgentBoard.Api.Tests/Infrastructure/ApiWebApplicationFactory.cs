// SPDX-License-Identifier: MIT
using AgentBoard.Infrastructure.Persistence;
using AgentBoard.Infrastructure.Persistence.Interceptors;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Infrastructure;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;

namespace AgentBoard.Api.Tests.Infrastructure;

/// <summary>
/// Gives each API test class an isolated database so xUnit can start
/// multiple application hosts in parallel without racing on SQLite schema creation.
/// </summary>
public sealed class ApiWebApplicationFactory : WebApplicationFactory<Program>
{
    private readonly string _databasePath = Path.Combine(
        Path.GetTempPath(),
        $"agentboard-api-tests-{Guid.NewGuid():N}.db");

    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        builder.UseEnvironment("Development");
        builder.ConfigureServices(services =>
        {
            services.RemoveAll<DbContextOptions<AppDbContext>>();
            services.RemoveAll<IDbContextOptionsConfiguration<AppDbContext>>();
            services.AddDbContext<AppDbContext>((serviceProvider, options) =>
            {
                options.UseSqlite($"Data Source={_databasePath}");
                options.AddInterceptors(
                    serviceProvider.GetRequiredService<AuditFieldsInterceptor>(),
                    serviceProvider.GetRequiredService<SoftDeleteInterceptor>(),
                    serviceProvider.GetRequiredService<DomainEventDispatcherInterceptor>());
            });
        });
    }

    protected override void Dispose(bool disposing)
    {
        base.Dispose(disposing);
        if (disposing && File.Exists(_databasePath))
        {
            File.Delete(_databasePath);
        }
    }
}
