// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Base;
using AgentBoard.Api.Api.Common;
using AgentBoard.Api.Clients;
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
	private readonly FastApiTaskClient _fastApiTaskClient;

	public TasksController(
		IBoardProvider provider,
		ICurrentUser current,
		FastApiTaskClient fastApiTaskClient) : base(provider, current)
	{
		_fastApiTaskClient = fastApiTaskClient ?? throw new ArgumentNullException(nameof(fastApiTaskClient));
	}

	[HttpGet]
	[ProducesResponseType(typeof(IReadOnlyList<TaskItemDto>), 200)]
	public async Task<ActionResult<IReadOnlyList<TaskItemDto>>> List(
		[FromQuery(Name = "project_id")] int? projectId,
		[FromQuery(Name = "story_id")] int? storyId, CancellationToken ct) =>
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
		[FromQuery(Name = "story_id")] int storyId,
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
		[FromBody] BulkTaskUpdateRequest? body,
		CancellationToken ct)
	{
		if (body is null || body.TaskIds is null)
			return UnprocessableEntity(new ApiError("task_ids is required and must not be empty"));
		var result = await Provider.BulkUpdateTasksAsync(body.TaskIds, body.Status, body.Priority, body.AssigneeId, body.DueDate, ct);
		return Ok(result);
	}

	[HttpDelete("bulk")]
	[ProducesResponseType(200)]
	[ProducesResponseType(typeof(ApiError), 422)]
	public async Task<IActionResult> BulkDelete(
		[FromBody] BulkTaskUpdateRequest? body,
		CancellationToken ct)
	{
		if (body is null || body.TaskIds is null)
			return UnprocessableEntity(new ApiError("task_ids is required and must not be empty"));
		var count = await Provider.BulkDeleteTasksAsync(body.TaskIds, ct);
		return Ok(new { deleted = count });
	}

	// ===================== P6: AI task generation (BFF module 6, 2026-08-23) =====================

	/// <summary>Proxies task generation to the FastAPI source of truth.</summary>
	[HttpPost("{id:int}/generate-subtasks")]
	[ProducesResponseType(200)]
	[ProducesResponseType(typeof(ApiError), 404)]
	[ProducesResponseType(typeof(ApiError), 502)]
    public async Task<IActionResult> GenerateSubtasks(
        int id,
        [FromBody] GenerateSubtasksRequest? _,
        CancellationToken ct)
    {
        // The downstream FastAPI call must not become an authorization bypass:
        // resolve the task through the same project access boundary first.
        if (await Provider.GetTaskAsync(id, ct) is null)
            return NotFound(new ApiError($"task {id} not found"));
        var response = await _fastApiTaskClient.ProxyGenerateSubtasksAsync(id, ct);
		return new ContentResult
		{
			StatusCode = (int)response.StatusCode,
			ContentType = response.ContentType,
			Content = response.Body,
		};
	}
}
