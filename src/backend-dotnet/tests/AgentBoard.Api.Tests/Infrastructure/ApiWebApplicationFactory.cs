// SPDX-License-Identifier: MIT
using AgentBoard.Api.Durable;
using AgentBoard.Infrastructure.Persistence;
using AgentBoard.Infrastructure.Persistence.Interceptors;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Infrastructure;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Hosting;

namespace AgentBoard.Api.Tests.Infrastructure;

/// <summary>
/// Gives each API test class an isolated database so xUnit can start
/// multiple application hosts in parallel without racing on SQLite schema creation.
/// </summary>
public sealed class ApiWebApplicationFactory : WebApplicationFactory<Program>
{
    private readonly bool _durableEnabled;
    private readonly string _databasePath = Path.Combine(
        Path.GetTempPath(),
        $"agentboard-api-tests-{Guid.NewGuid():N}.db");
    private readonly string _durableDatabasePath = Path.Combine(
        Path.GetTempPath(),
        $"agentboard-durable-api-tests-{Guid.NewGuid():N}.db");

    public ApiWebApplicationFactory() : this(false) { }

    private ApiWebApplicationFactory(bool durableEnabled) =>
        _durableEnabled = durableEnabled;

    public static ApiWebApplicationFactory CreateDurable() => new(true);

    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        // "Testing" tells Serilog/OpenTelemetry to skip the file sink and
        // console-exporter overhead — keeps the per-test host lean.
        builder.UseEnvironment("Testing");

        builder.ConfigureAppConfiguration((_, configuration) =>
            configuration.AddInMemoryCollection(new Dictionary<string, string?>
            {
                ["DurableWorkflow:Enabled"] = _durableEnabled.ToString(),
                ["DurableWorkflow:DatabasePath"] = _durableDatabasePath,
                ["DurableWorkflow:RabbitMqUri"] = "amqp://guest:guest@localhost:5672/",
            }));

        builder.ConfigureServices(services =>
        {
            if (_durableEnabled)
            {
                var durableWorkers = services
                    .Where(descriptor => descriptor.ServiceType == typeof(IHostedService)
                        && descriptor.ImplementationType is not null
                        && (descriptor.ImplementationType == typeof(DurableServerOutboxService)
                            || descriptor.ImplementationType == typeof(DurableServerResultConsumerService)))
                    .ToList();
                foreach (var descriptor in durableWorkers) services.Remove(descriptor);
            }

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
        if (!disposing)
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
                foreach (var databasePath in new[] { _databasePath, _durableDatabasePath })
                {
                    File.Delete(databasePath);
                    File.Delete(databasePath + "-wal");
                    File.Delete(databasePath + "-shm");
                }
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
