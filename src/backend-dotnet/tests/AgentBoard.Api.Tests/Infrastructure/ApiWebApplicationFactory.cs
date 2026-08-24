// SPDX-License-Identifier: MIT
using AgentBoard.Infrastructure.Persistence;
using AgentBoard.Infrastructure.Persistence.Interceptors;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Infrastructure;
using Microsoft.Extensions.Configuration;
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
        // "Testing" tells Serilog/OpenTelemetry to skip the file sink and
        // console-exporter overhead — keeps the per-test host lean.
        builder.UseEnvironment("Testing");

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
        if (!disposing || !File.Exists(_databasePath))
        {
            return;
        }

        // SQLite e_sqlite3 sometimes holds the file handle via mmap until
        // GC finalizers run, so a plain File.Delete on Windows can race and
        // fail with IOException. Best-effort retry with a short delay keeps
        // xUnit's Test Class Cleanup quiet without masking real failures.
        for (var attempt = 0; attempt < 5; attempt++)
        {
            try
            {
                File.Delete(_databasePath);
                return;
            }
            catch (IOException) when (attempt < 4)
            {
                Thread.Sleep(50);
            }
            catch
            {
                // Give up silently — temp file will be reaped by the OS.
                return;
            }
        }
    }
}
