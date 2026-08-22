// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Base;
using AgentBoard.Api.Api.Common;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Tasks;

/// <summary>Read-only task endpoints. Mirrors FastAPI <c>/api/tasks</c>.</summary>
[ApiController]
[Route("api/tasks")]
[Produces("application/json")]
public sealed class TasksController : BaseController<IBoardProvider>
{
    public TasksController(IBoardProvider provider, ICurrentUser current) : base(provider, current) { }

    [HttpGet]
    [ProducesResponseType(typeof(IReadOnlyList<AgentBoard.Application.Board.Dtos.TaskItemDto>), 200)]
    public async Task<ActionResult<IReadOnlyList<AgentBoard.Application.Board.Dtos.TaskItemDto>>> List(
        [FromQuery] int? projectId, [FromQuery] int? storyId, CancellationToken ct) =>
        Ok(await Provider.ListTasksAsync(projectId, storyId, ct));

    [HttpGet("{id:int}")]
    [ProducesResponseType(typeof(AgentBoard.Application.Board.Dtos.TaskItemDto), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult<AgentBoard.Application.Board.Dtos.TaskItemDto>> Get(int id, CancellationToken ct)
    {
        var dto = await Provider.GetTaskAsync(id, ct);
        return dto is null ? NotFound(new ApiError($"task {id} not found")) : Ok(dto);
    }
}
