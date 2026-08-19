// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using NetArchTest.Rules;

namespace AgentBoard.Infrastructure.Tests.Architecture;

/// <summary>
/// NetArchTest rules enforcing the 5-layer separation documented in
/// <c>openspec/.../code-structure.md</c>. If any of these fail the
/// layering has been violated; treat as a hard error before merging.
///
/// Rules:
///   1. Controllers depend only on IProvider (and ICurrentUser / DTOs).
///   2. Providers depend only on Services (not IRepository / IDbContext).
///   3. Services depend only on IRepository / IUnitOfWork / IClock / ICurrentUser.
///   4. IRepository implementations live only in Infrastructure.
///   5. Application has no dependency on Infrastructure / Api / EF Core.
///   6. Domain has no dependency on any other layer / ASP.NET / EF Core.
/// </summary>
public sealed class LayeredArchitectureTests
{
    private const string ApplicationNs = "AgentBoard.Application";
    private const string InfrastructureNs = "AgentBoard.Infrastructure";
    private const string ApiNs = "AgentBoard.Api";
    private const string DomainNs = "AgentBoard.Domain";

    [Fact]
    public void Controllers_DoNot_DependOn_Repository_Or_DbContext()
    {
        // Match by type FullName so we don't false-positive on ICurrentUser
        // (which lives in the same namespace as IRepository<T>).
        var result = Types.InAssembly(typeof(AgentBoard.Api.AssemblyMarker).Assembly)
            .That().ResideInNamespace($"{ApiNs}.Features")
            .And().HaveNameEndingWith("Controller")
            .ShouldNot()
            .HaveDependencyOnAny(
                "AgentBoard.Application.Abstractions.IRepository`1",
                "AgentBoard.Application.Abstractions.IDbContext",
                "AgentBoard.Application.Abstractions.IUnitOfWork",
                "Microsoft.EntityFrameworkCore")
            .GetResult();

        Assert.True(result.IsSuccessful,
            "Controllers must not depend on IRepository / IDbContext / IUnitOfWork or EF Core.\n" +
            string.Join("\n", result.FailingTypeNames ?? Array.Empty<string>()));
    }

    [Fact]
    public void Controllers_DoNot_DependOn_Services_Directly()
    {
        // Controllers go through IProvider only. Direct IService usage would
        // skip the Provider layer (no transaction boundary, no event pub).
        var result = Types.InAssembly(typeof(AgentBoard.Api.AssemblyMarker).Assembly)
            .That().ResideInNamespace($"{ApiNs}.Features")
            .And().HaveNameEndingWith("Controller")
            .ShouldNot()
            .HaveDependencyOn("AgentBoard.Application.Identity.UserService")
            .GetResult();

        Assert.True(result.IsSuccessful,
            "Controllers must call Providers, not Services directly.\n" +
            string.Join("\n", result.FailingTypeNames ?? Array.Empty<string>()));
    }

    [Fact]
    public void Application_HasNo_DependencyOn_Infrastructure_Or_Api()
    {
        var result = Types.InAssembly(typeof(IProvider).Assembly)
            .ShouldNot()
            .HaveDependencyOnAny(InfrastructureNs, ApiNs, "Microsoft.EntityFrameworkCore")
            .GetResult();

        Assert.True(result.IsSuccessful,
            "Application layer must not depend on Infrastructure / Api / EF Core.\n" +
            string.Join("\n", result.FailingTypeNames ?? Array.Empty<string>()));
    }

    [Fact]
    public void Domain_HasNo_DependencyOn_Any_Other_Layer()
    {
        var result = Types.InAssembly(typeof(AgentBoard.Domain.Common.Entity).Assembly)
            .ShouldNot()
            .HaveDependencyOnAny(ApplicationNs, InfrastructureNs, ApiNs,
                "Microsoft.EntityFrameworkCore", "Microsoft.AspNetCore")
            .GetResult();

        Assert.True(result.IsSuccessful,
            "Domain layer must be pure C# with no external references.\n" +
            string.Join("\n", result.FailingTypeNames ?? Array.Empty<string>()));
    }

    [Fact]
    public void IRepository_Implementations_Live_Only_In_Infrastructure()
    {
        var result = Types.InAssembly(typeof(AgentBoard.Infrastructure.Persistence.AppDbContext).Assembly)
            .That().ImplementInterface(typeof(IRepository<>))
            .Should()
            .ResideInNamespaceStartingWith(InfrastructureNs)
            .GetResult();

        Assert.True(result.IsSuccessful,
            "IRepository<T> implementations must live in Infrastructure.\n" +
            string.Join("\n", result.FailingTypeNames ?? Array.Empty<string>()));
    }
}
