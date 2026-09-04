// SPDX-License-Identifier: MIT
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.Filters;
using Microsoft.Extensions.Options;

namespace AgentBoard.Api.Durable;

/// <summary>Fail-closed API gate matching the durable background services.</summary>
public sealed class DurableWorkflowGateFilter : IAsyncActionFilter
{
    private readonly IOptionsMonitor<DurableWorkflowOptions> _options;

    public DurableWorkflowGateFilter(IOptionsMonitor<DurableWorkflowOptions> options) => _options = options;

    public async Task OnActionExecutionAsync(ActionExecutingContext context, ActionExecutionDelegate next)
    {
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
