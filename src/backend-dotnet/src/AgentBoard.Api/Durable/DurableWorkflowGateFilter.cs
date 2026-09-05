// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.Filters;
using Microsoft.Extensions.Options;

namespace AgentBoard.Api.Durable;

/// <summary>
/// Fail-closed gate for the durable control plane. Enforces, in order: (1) the
/// caller must be authenticated, (2) the feature must be enabled. This BFF
/// registers no ASP.NET Core authentication scheme (<c>AddAuthentication</c> is
/// absent), so an MVC <c>[Authorize]</c> would throw 500 on the challenge path;
/// instead we mirror the codebase's own auth idiom
/// (ApiKeysController / RealtimeNotificationsController) and read the caller
/// resolved by <c>Auth.AuthMiddleware</c> via <see cref="ICurrentUser"/>. The
/// auth check runs BEFORE the enabled check so an anonymous request never
/// learns whether durable is on or off.
/// </summary>
public sealed class DurableWorkflowGateFilter : IAsyncActionFilter
{
    private readonly IOptionsMonitor<DurableWorkflowOptions> _options;
    private readonly ICurrentUser _currentUser;

    public DurableWorkflowGateFilter(
        IOptionsMonitor<DurableWorkflowOptions> options,
        ICurrentUser currentUser)
    {
        _options = options;
        _currentUser = currentUser;
    }

    public async Task OnActionExecutionAsync(ActionExecutingContext context, ActionExecutionDelegate next)
    {
        // 1) Authentication required for every durable endpoint (Bearer v1 token
        //    or a valid abk_ API key). Null UserId => AuthMiddleware resolved no
        //    caller => reject.
        if (_currentUser.UserId is null)
        {
            context.Result = new ObjectResult(new ProblemDetails
            {
                Status = StatusCodes.Status401Unauthorized,
                Title = "authentication required",
                Detail = "Durable workflow endpoints require a valid bearer token or API key.",
            })
            {
                StatusCode = StatusCodes.Status401Unauthorized,
            };
            return;
        }

        // 2) Feature gate.
        if (_options.CurrentValue.Enabled)
        {
            await next();
            return;
        }

        context.HttpContext.Response.Headers["Retry-After"] = "30";
        context.Result = new ObjectResult(new ProblemDetails
        {
            Status = StatusCodes.Status503ServiceUnavailable,
            Title = "Durable workflow is disabled",
            Detail = "Enable DurableWorkflow:Enabled before using the durable control plane.",
        })
        {
            StatusCode = StatusCodes.Status503ServiceUnavailable,
        };
    }
}
