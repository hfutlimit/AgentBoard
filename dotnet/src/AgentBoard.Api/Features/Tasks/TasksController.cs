// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Base;
using AgentBoard.Api.Api.Common;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board;
using AgentBoard.Application.Board.Dtos;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Tasks;

/// <summary>Task endpoints. Mirrors FastAPI <c>/api/tasks</c>.</summary>
[ApiController]
[Route("api/tasks")]
[Produces("application/json")]
public sealed class TasksController : BaseController<IBoardProvider>
{
    public TasksController(IBoardProvider provider, ICurrentUser current) : base(provider, current) { }

    [HttpGet]
    [ProducesResponseType(typeof(IReadOnlyList<TaskItemDto>), 200)]
    public async Task<ActionResult<IReadOnlyList<TaskItemDto>>> List(
        [FromQuery] int? projectId, [FromQuery] int? storyId, CancellationToken ct) =>
        Ok(await Provider.ListTasksAsync(projectId, storyId, ct));

    [HttpGet("{id:int}")]
    [ProducesResponseType(typeof(TaskItemDto), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult<TaskItemDto>> Get(int id, CancellationToken ct)
    {
        var dto = await Provider.GetTaskAsync(id, ct);
        return dto is null ? NotFound(new ApiError($"task {id} not found")) : Ok(dto);
    }

    [HttpPost]
    [ProducesResponseType(typeof(TaskItemDto), 201)]
    [ProducesResponseType(typeof(ApiError), 404)]
    [ProducesResponseType(typeof(ApiError), 422)]
    public async Task<ActionResult<TaskItemDto>> Create(
        [FromQuery] int storyId,
        [FromBody] TaskCreateRequest body,
        CancellationToken ct)
    {
        var dto = await Provider.CreateTaskAsync(
            storyId, body.Type, body.Title, body.Priority, body.Description, body.Spec, body.AssigneeId, ct);
        return CreatedAtAction(nameof(Get), new { id = dto.Id }, dto);
    }

    [HttpPatch("{id:int}")]
    [ProducesResponseType(typeof(TaskItemDto), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult<TaskItemDto>> Update(
        int id,
        [FromBody] TaskPatchRequest body,
        CancellationToken ct)
    {
        var dto = await Provider.UpdateTaskAsync(
            id, body.Type, body.Title, body.Status, body.Priority, body.StatusReason,
            body.Description, body.Spec, body.AssigneeId, body.DueDate, body.Labels,
            body.Estimate, body.Complexity, body.NeededCapabilities, body.DomainTags,
            body.SprintId, body.ReviewerId, ct);
        return dto is null ? NotFound(new ApiError($"task {id} not found")) : Ok(dto);
    }

    [HttpDelete("{id:int}")]
    [ProducesResponseType(204)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<IActionResult> Delete(int id, CancellationToken ct)
    {
        var deleted = await Provider.DeleteTaskAsync(id, ct);
        return deleted ? NoContent() : NotFound(new ApiError($"task {id} not found"));
    }

    [HttpPut("{id:int}/status")]
    [ProducesResponseType(typeof(TaskItemDto), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    [ProducesResponseType(typeof(ApiError), 422)]
    public async Task<ActionResult<TaskItemDto>> UpdateStatus(
        int id,
        [FromBody] TaskStatusRequest body,
        CancellationToken ct)
    {
        var dto = await Provider.UpdateTaskStatusAsync(id, body.Status, body.StatusReason, ct);
        return dto is null ? NotFound(new ApiError($"task {id} not found")) : Ok(dto);
    }

    [HttpPatch("bulk")]
    [ProducesResponseType(typeof(IReadOnlyList<TaskItemDto>), 200)]
    [ProducesResponseType(typeof(ApiError), 422)]
    public async Task<ActionResult<IReadOnlyList<TaskItemDto>>> BulkUpdate(
        [FromBody] BulkTaskUpdateRequest body,
        CancellationToken ct)
    {
        var result = await Provider.BulkUpdateTasksAsync(body.TaskIds, body.Status, body.Priority, body.AssigneeId, body.DueDate, ct);
        return Ok(result);
    }

    [HttpDelete("bulk")]
    [ProducesResponseType(200)]
    [ProducesResponseType(typeof(ApiError), 422)]
    public async Task<IActionResult> BulkDelete(
        [FromBody] BulkTaskUpdateRequest body,
        CancellationToken ct)
    {
        var count = await Provider.BulkDeleteTasksAsync(body.TaskIds, ct);
        return Ok(new { deleted = count });
    }

    // ===================== P6: AI task generation (BFF module 6, 2026-08-23) =====================

    /// <summary>
    /// Generate AI-suggested sub-tasks under a Task. Mirrors FastAPI
    /// <c>POST /api/tasks/{tid}/generate-subtasks</c>. The .NET BFF currently
    /// returns stub "Subtask {i}" rows (TODO: integrate AI service in stage 2).
    /// Response envelope: <c>{"generated": [...]}</c>.
    /// </summary>
    [HttpPost("{id:int}/generate-subtasks")]
    [ProducesResponseType(typeof(GenerateSubtasksResponse), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult<GenerateSubtasksResponse>> GenerateSubtasks(
        int id,
        [FromBody] GenerateSubtasksRequest? body,
        CancellationToken ct)
    {
        var count = body?.Count ?? 5;
        var created = await Provider.GenerateSubtasksAsync(id, count, ct);
        return Ok(new GenerateSubtasksResponse(created));
    }
}
