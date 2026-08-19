// SPDX-License-Identifier: MIT
using Microsoft.AspNetCore.Mvc.ApplicationModels;

namespace AgentBoard.Api.Api.Conventions;

/// <summary>
/// Strips any leading "Api" / "api-" prefix from controller names so that
/// <c>AuthController</c> routes to <c>/api/auth</c> rather than
/// <c>/api/apiauth</c>. This is the same behaviour the FastAPI version
/// implements by way of its <c>@router.get("/auth/...")</c> decorators.
/// </summary>
public sealed class ApiRouteConvention : IControllerModelConvention
{
    public void Apply(ControllerModel controller)
    {
        ArgumentNullException.ThrowIfNull(controller);
        if (controller.ControllerName.StartsWith("Api", StringComparison.Ordinal))
        {
            controller.ControllerName = controller.ControllerName[3..];
        }
    }
}
