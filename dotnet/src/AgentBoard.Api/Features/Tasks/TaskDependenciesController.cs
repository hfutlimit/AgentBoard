// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Base;
using AgentBoard.Api.Api.Common;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board;
using AgentBoard.Application.Board.Dtos;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Tasks;

/// <summary>Task dependency endpoints. Mirrors FastAPI <c>/api/tasks/{taskId}/dependencies</c>.</summary>
[ApiController]
[Route("api/tasks/{taskId:int}/dependencies")]
[Produces("application/json")]
public sealed class TaskDependenciesController : BaseController<IBoardProvider>
{
    public TaskDependenciesController(IBoardProvider provider, ICurrentUser current) : base(provider, current) { }

    [HttpGet]
    [ProducesResponseType(typeof(IReadOnlyList<TaskDependencyDto>), 200)]
    public async Task<ActionResult<IReadOnlyList<TaskDependencyDto>>> List(
        int taskId, CancellationToken ct) =>
        Ok(await Provider.GetTaskDependenciesAsync(taskId, ct));

    [HttpPost]
    [ProducesResponseType(typeof(TaskDependencyDto), 201)]
    [ProducesResponseType(typeof(ApiError), 404)]
    [ProducesResponseType(typeof(ApiError), 422)]
    public async Task<ActionResult<TaskDependencyDto>> Create(
        int taskId,
        [FromBody] DependencyCreateRequest body,
        CancellationToken ct)
    {
        var dto = await Provider.AddTaskDependencyAsync(taskId, body.DependsOnId, body.DependencyType, ct);
        return CreatedAtAction(nameof(List), new { taskId }, dto);
    }

    [HttpDelete("{id:int}")]
    [ProducesResponseType(204)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<IActionResult> Delete(int id, CancellationToken ct)
    {
        var deleted = await Provider.RemoveTaskDependencyAsync(id, ct);
        return deleted ? NoContent() : NotFound(new ApiError($"dependency {id} not found"));
    }
}
