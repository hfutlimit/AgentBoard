// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Base;
using AgentBoard.Api.Api.Common;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Projects;

/// <summary>Read-only project endpoints. Mirrors FastAPI <c>/api/projects</c>.</summary>
[ApiController]
[Route("api/projects")]
[Produces("application/json")]
public sealed class ProjectsController : BaseController<IBoardProvider>
{
    public ProjectsController(IBoardProvider provider, ICurrentUser current) : base(provider, current) { }

    [HttpGet]
    [ProducesResponseType(typeof(IReadOnlyList<AgentBoard.Application.Board.Dtos.ProjectDto>), 200)]
    public async Task<ActionResult<IReadOnlyList<AgentBoard.Application.Board.Dtos.ProjectDto>>> List(CancellationToken ct) =>
        Ok(await Provider.ListProjectsAsync(ct));

    [HttpGet("{id:int}")]
    [ProducesResponseType(typeof(AgentBoard.Application.Board.Dtos.ProjectDto), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult<AgentBoard.Application.Board.Dtos.ProjectDto>> Get(int id, CancellationToken ct)
    {
        var dto = await Provider.GetProjectAsync(id, ct);
        return dto is null ? NotFound(new ApiError($"project {id} not found")) : Ok(dto);
    }
}
