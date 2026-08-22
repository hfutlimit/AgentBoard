// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Base;
using AgentBoard.Api.Api.Common;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Epics;

/// <summary>Read-only epic endpoints. Mirrors FastAPI <c>/api/epics</c>.</summary>
[ApiController]
[Route("api/epics")]
[Produces("application/json")]
public sealed class EpicsController : BaseController<IBoardProvider>
{
    public EpicsController(IBoardProvider provider, ICurrentUser current) : base(provider, current) { }

    [HttpGet]
    [ProducesResponseType(typeof(IReadOnlyList<AgentBoard.Application.Board.Dtos.EpicDto>), 200)]
    public async Task<ActionResult<IReadOnlyList<AgentBoard.Application.Board.Dtos.EpicDto>>> List(
        [FromQuery] int? projectId, CancellationToken ct) =>
        Ok(await Provider.ListEpicsAsync(projectId, ct));

    [HttpGet("{id:int}")]
    [ProducesResponseType(typeof(AgentBoard.Application.Board.Dtos.EpicDto), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult<AgentBoard.Application.Board.Dtos.EpicDto>> Get(int id, CancellationToken ct)
    {
        var dto = await Provider.GetEpicAsync(id, ct);
        return dto is null ? NotFound(new ApiError($"epic {id} not found")) : Ok(dto);
    }
}
