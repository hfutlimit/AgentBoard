// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Domain.Identity;
using AgentBoard.Infrastructure.Persistence;
using AgentBoard.Infrastructure.Persistence.Interceptors;
using AgentBoard.Infrastructure.Persistence.Repositories;
using AgentBoard.Infrastructure.Time;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Diagnostics;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;

namespace AgentBoard.Infrastructure;

/// <summary>
/// Wires up the persistence + cross-cutting services into the DI container.
/// Called from <c>Program.cs</c> via <c>builder.Services.AddInfrastructure(...)</c>.
///
/// Provider switch:
///   - <c>memory</c> / <c>inmemory</c> — in-memory database (unit tests)
///   - <c>sqlite</c>                   — local dev convenience
///   - <c>mysql</c> / <c>mariadb</c>   — production; requires
///     <c>Pomelo.EntityFrameworkCore.MySql</c> package
/// </summary>
public static class DependencyInjection
{
    public static IServiceCollection AddInfrastructure(
        this IServiceCollection services,
        IConfiguration configuration)
    {
        ArgumentNullException.ThrowIfNull(services);
        ArgumentNullException.ThrowIfNull(configuration);

        // --- Time --------------------------------------------------------
        services.TryAddSingleton<IClock, SystemClock>();

        // --- Persistence -------------------------------------------------
        var provider = configuration.GetValue<string>("AgentBoard:Database:Provider") ?? "sqlite";
        var connectionString = configuration.GetValue<string>("AgentBoard:Database:ConnectionString");
        if (string.IsNullOrWhiteSpace(connectionString))
        {
            // Last-resort fallback for environments that don't bring their
            // own appsettings.Development.json (e.g. WebApplicationFactory
            // bootstrapping the test host). Production must always set the
            // connection string explicitly via env var or appsettings.
            connectionString = "Data Source=agentboard-dotnet.db";
            provider = "sqlite";
        }

        services.AddDbContext<AppDbContext>((sp, options) =>
        {
            // Three interceptors are registered as scoped services so they can
            // pull the current request's IClock and ICurrentUser.
            options.AddInterceptors(
                sp.GetRequiredService<AuditFieldsInterceptor>(),
                sp.GetRequiredService<SoftDeleteInterceptor>(),
                sp.GetRequiredService<DomainEventDispatcherInterceptor>());

            switch (provider.ToLowerInvariant())
            {
                case "memory":
                case "inmemory":
                    options.UseInMemoryDatabase(connectionString);
                    break;
                case "sqlite":
                    options.UseSqlite(connectionString);
                    break;
                case "mysql":
                case "mariadb":
                    // NOTE: Pomelo.EntityFrameworkCore.MySql 10.0.0 is not
                    // published as of 2026-08-19. The MySQL branch is
                    // intentionally left as a TODO until Pomelo ships.
                    // Until then, dev/staging use sqlite and production stays
                    // on FastAPI; the .NET API runs against a local sqlite
                    // shadow for contract tests (see S0-5).
                    throw new NotSupportedException(
                        "MySQL provider not yet wired in .NET BFF — Pomelo 10.0.0 is not " +
                        "available. Use sqlite for now. " +
                        "Tracking: dual-stack-bff-restructure/Story 308 follow-up.");
                default:
                    throw new InvalidOperationException(
                        $"Unsupported database provider '{provider}'. Use memory / sqlite (mysql pending Pomelo 10.0).");
            }
        });

        services.AddScoped<IDbContext>(sp => sp.GetRequiredService<AppDbContext>());
        services.AddScoped<IUnitOfWork>(sp => sp.GetRequiredService<AppDbContext>());

        // --- Repositories ------------------------------------------------
        services.AddScoped<IUserRepository, UserRepository>();
        services.AddScoped<IProjectRepository, ProjectRepository>();
        services.AddScoped<IEpicRepository, EpicRepository>();
        services.AddScoped<IStoryRepository, StoryRepository>();
        services.AddScoped<ITaskItemRepository, TaskItemRepository>();
        services.AddScoped<ICommentRepository, CommentRepository>();
        services.AddScoped<IProjectMemberRepository, ProjectMemberRepository>();
        services.AddScoped<INotificationRepository, NotificationRepository>();
        services.AddScoped<ISprintRepository, SprintRepository>();
        services.AddScoped<IAttachmentRepository, AttachmentRepository>();
        services.AddScoped<IAuditLogRepository, AuditLogRepository>();
        services.AddScoped<ITaskDependencyRepository, TaskDependencyRepository>();
        services.AddScoped<IWebhookConfigRepository, WebhookConfigRepository>();
        services.AddScoped<IApiKeyRepository, ApiKeyRepository>();
        services.AddScoped<IDocumentRepository, DocumentRepository>();
        services.AddScoped<IDocumentRevisionRepository, DocumentRevisionRepository>();
        services.AddScoped<IDocumentFolderRepository, DocumentFolderRepository>();
        services.AddScoped<IDocumentCommentRepository, DocumentCommentRepository>();
        services.AddScoped<IStoryStatusHistoryRepository, StoryStatusHistoryRepository>();
        services.AddScoped<ITaskStatusHistoryRepository, TaskStatusHistoryRepository>();

        // --- Interceptors ------------------------------------------------
        services.AddScoped<AuditFieldsInterceptor>();
        services.AddScoped<SoftDeleteInterceptor>();
        services.AddScoped<DomainEventDispatcherInterceptor>();

        return services;
    }
}
