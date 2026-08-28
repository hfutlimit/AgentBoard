// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Base;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board;
using AgentBoard.Application.Board.Dtos;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Tasks;

/// <summary>Task search endpoint. Mirrors FastAPI <c>/api/tasks</c> GET with query filters.
/// This controller is at a higher route precedence (or merged into TasksController)
/// so that the search params are available without conflicting with the basic List.</summary>
[ApiController]
[Route("api/tasks")]
[Produces("application/json")]
public sealed class TaskSearchController : BaseController<IBoardProvider>
{
    public TaskSearchController(IBoardProvider provider, ICurrentUser current) : base(provider, current) { }

    /// <summary>Search tasks with text query and filters.</summary>
    [HttpGet("search")]
    [ProducesResponseType(typeof(IReadOnlyList<TaskItemDto>), 200)]
    public async Task<ActionResult<IReadOnlyList<TaskItemDto>>> Search(
        [FromQuery] string? q,
        [FromQuery(Name = "project_id")] int? projectId,
        [FromQuery(Name = "story_id")] int? storyId,
        [FromQuery] string? status,
        [FromQuery] string? priority,
        [FromQuery] string? assigneeId,
        [FromQuery] int limit = 50,
        CancellationToken ct = default) =>
        Ok(await Provider.SearchTasksAsync(
            new SearchTasksQuery(q, projectId, storyId, status, priority, assigneeId, limit), ct));
}
