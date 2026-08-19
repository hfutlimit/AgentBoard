// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Common;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.Filters;

namespace AgentBoard.Api.Api.Common;

/// <summary>
/// Maps domain exceptions to HTTP status codes once, globally. Controllers
/// stay free of try/catch boilerplate.
///
/// Mapping (matches the FastAPI service.py handlers):
///   <see cref="NotFoundException"/>           → 404
///   <see cref="DuplicateException"/>          → 409
///   <see cref="InvalidValueException"/>       → 422
///   <see cref="IllegalTransitionException"/>  → 400
///   anything else <see cref="DomainException"/>→ 500
/// </summary>
public sealed class DomainExceptionFilter : IExceptionFilter
{
    private readonly ILogger<DomainExceptionFilter> _logger;

    public DomainExceptionFilter(ILogger<DomainExceptionFilter> logger) =>
        _logger = logger ?? throw new ArgumentNullException(nameof(logger));

    public void OnException(ExceptionContext context)
    {
        ArgumentNullException.ThrowIfNull(context);
        if (context.Exception is not DomainException ex) return;

        var (status, code) = ex switch
        {
            NotFoundException => (StatusCodes.Status404NotFound, "not_found"),
            DuplicateException => (StatusCodes.Status409Conflict, "duplicate"),
            InvalidValueException => (StatusCodes.Status422UnprocessableEntity, "invalid_value"),
            IllegalTransitionException => (StatusCodes.Status400BadRequest, "illegal_transition"),
            _ => (StatusCodes.Status500InternalServerError, "domain_error"),
        };

        if (status >= 500)
            _logger.LogError(ex, "Unhandled domain exception");
        else
            _logger.LogDebug("Domain exception: {Message}", ex.Message);

        context.Result = new ObjectResult(new ApiError(ex.Message))
        {
            StatusCode = status,
        };
        context.ExceptionHandled = true;
    }
}
