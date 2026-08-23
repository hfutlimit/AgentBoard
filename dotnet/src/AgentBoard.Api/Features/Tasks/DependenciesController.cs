// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Base;
using AgentBoard.Api.Api.Common;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Tasks;

/// <summary>Dependency endpoint keyed by dependency id (no task prefix).
/// Mirrors FastAPI <c>/api/dependencies/{id}</c> used by the frontend
/// <c>removeTaskDependency</c> helper.</summary>
[ApiController]
[Route("api/dependencies")]
[Produces("application/json")]
public sealed class DependenciesController : BaseController<IBoardProvider>
{
    public DependenciesController(IBoardProvider provider, ICurrentUser current) : base(provider, current) { }

    [HttpDelete("{id:int}")]
    [ProducesResponseType(204)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<IActionResult> Delete(int id, CancellationToken ct)
    {
        var deleted = await Provider.RemoveTaskDependencyAsync(id, ct);
        return deleted ? NoContent() : NotFound(new ApiError($"dependency {id} not found"));
    }
}
