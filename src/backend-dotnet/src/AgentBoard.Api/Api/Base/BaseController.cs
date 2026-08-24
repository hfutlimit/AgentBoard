// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Common;
using AgentBoard.Application.Abstractions;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Api.Base;

/// <summary>
/// Root base controller. Exposes the current user to derived controllers
/// and a uniform <see cref="Problem(int,string)"/> helper for endpoints
/// that need to bail out before reaching a Provider (rare — most failures
/// flow through the <see cref="DomainExceptionFilter"/>).
/// </summary>
[ApiController]
[Route("api/[controller]")]
[Produces("application/json")]
public abstract class BaseController : ControllerBase
{
    /// <summary>Caller context resolved from the bearer token / API key.</summary>
    protected ICurrentUser CurrentUser { get; }

    protected BaseController(ICurrentUser current) =>
        CurrentUser = current ?? throw new ArgumentNullException(nameof(current));

    /// <summary>Standard 4xx/5xx error envelope.</summary>
    protected ObjectResult Problem(int statusCode, string detail) =>
        StatusCode(statusCode, new ApiError(detail));
}

/// <summary>
/// Generic base controller. Derived classes inject their Provider via
/// constructor and expose it through the protected <see cref="Provider"/>
/// property. The Provider is the only Application-layer type a Controller
/// may depend on — the architecture test in
/// <c>tests/.../Architecture/LayeredArchitectureTests</c> enforces this.
/// </summary>
/// <typeparam name="TProvider">An <see cref="IProvider"/>-tagged type
/// that the Controller will delegate to.</typeparam>
public abstract class BaseController<TProvider> : BaseController
    where TProvider : IProvider
{
    protected TProvider Provider { get; }

    protected BaseController(TProvider provider, ICurrentUser current) : base(current)
    {
        Provider = provider ?? throw new ArgumentNullException(nameof(provider));
    }
}
