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
	[ProducesResponseType(typeof(AgentBoard.Application.Board.Dtos.ProjectListResult), 200)]
	public async Task<ActionResult<AgentBoard.Application.Board.Dtos.ProjectListResult>> List(
		[FromQuery] int limit = 100,
		[FromQuery] int offset = 0,
		[FromQuery(Name = "include_archived")] bool? includeArchived = null,
		CancellationToken ct = default) =>
		Ok(await Provider.ListProjectsAsync(Math.Clamp(limit, 1, 200), Math.Max(0, offset), includeArchived, ct));

	[HttpGet("{id:int}")]
	[ProducesResponseType(typeof(AgentBoard.Application.Board.Dtos.ProjectDto), 200)]
	[ProducesResponseType(typeof(ApiError), 404)]
	public async Task<ActionResult<AgentBoard.Application.Board.Dtos.ProjectDto>> Get(int id, CancellationToken ct)
	{
		var dto = await Provider.GetProjectAsync(id, ct);
		return dto is null ? NotFound(new ApiError($"project {id} not found")) : Ok(dto);
	}

	[HttpGet("{id:int}/stats")]
	[ProducesResponseType(typeof(AgentBoard.Application.Board.Dtos.ProjectStatsDto), 200)]
	[ProducesResponseType(typeof(ApiError), 404)]
	public async Task<ActionResult<AgentBoard.Application.Board.Dtos.ProjectStatsDto>> Stats(int id, CancellationToken ct)
	{
		var dto = await Provider.GetProjectStatsAsync(id, ct);
		return dto is null ? NotFound(new ApiError($"project {id} not found")) : Ok(dto);
	}

	[HttpGet("{id:int}/kanban")]
	[ProducesResponseType(typeof(AgentBoard.Application.Board.Dtos.KanbanDto), 200)]
	[ProducesResponseType(typeof(ApiError), 404)]
	public async Task<ActionResult<AgentBoard.Application.Board.Dtos.KanbanDto>> Kanban(
		int id, [FromQuery(Name = "include_all")] bool includeAll = false, CancellationToken ct = default)
	{
		var dto = await Provider.GetProjectKanbanAsync(id, includeAll, ct);
		return dto is null ? NotFound(new ApiError($"project {id} not found")) : Ok(dto);
	}

	[HttpGet("{id:int}/members")]
	[ProducesResponseType(typeof(AgentBoard.Application.Board.Dtos.ProjectMembersResult), 200)]
	[ProducesResponseType(typeof(ApiError), 404)]
	public async Task<ActionResult<AgentBoard.Application.Board.Dtos.ProjectMembersResult>> Members(
		int id, [FromQuery] int limit = 50, [FromQuery] int offset = 0, CancellationToken ct = default)
	{
		var dto = await Provider.ListProjectMembersAsync(id, limit, offset, ct);
		return dto is null ? NotFound(new ApiError($"project {id} not found")) : Ok(dto);
	}

	[HttpGet("center")]
	[ProducesResponseType(typeof(AgentBoard.Application.Board.Dtos.ProjectsCenterResult), 200)]
	public async Task<ActionResult<AgentBoard.Application.Board.Dtos.ProjectsCenterResult>> Center(
		[FromQuery] string scope = "active",
		[FromQuery] string sort = "recent",
		[FromQuery(Name = "include_archived")] bool? includeArchived = null,
		[FromQuery] int limit = 50,
		[FromQuery] int offset = 0,
		CancellationToken ct = default)
	{
		limit = Math.Clamp(limit, 1, 200);
		offset = Math.Max(0, offset);
		var result = await Provider.ListProjectsCenterAsync(
			CurrentUser.UserId, CurrentUser.IsAdmin, scope, sort, limit, offset, includeArchived, ct);
		return Ok(result);
	}

	[HttpGet("{projectId:int}/epics")]
	public async Task<ActionResult<IReadOnlyList<AgentBoard.Application.Board.Dtos.EpicDto>>> ProjectEpics(
		int projectId, [FromQuery] string? status = null, [FromQuery] int limit = 50,
		[FromQuery] int offset = 0, CancellationToken ct = default)
	{
		if (await Provider.GetProjectAsync(projectId, ct) is null)
			return NotFound(new ApiError($"project {projectId} not found"));
		return Ok(await Provider.ListProjectEpicsAsync(projectId, status, Math.Clamp(limit, 1, 200), Math.Max(0, offset), ct));
	}

	[HttpPost("{projectId:int}/epics")]
	public async Task<ActionResult<AgentBoard.Application.Board.Dtos.EpicDto>> ProjectEpic(
		int projectId, [FromBody] AgentBoard.Application.Board.Dtos.EpicCreateRequest body, CancellationToken ct)
	{
		var dto = await Provider.CreateProjectEpicAsync(projectId, body.Title ?? body.Name, body.Description, ct);
		return dto is null ? NotFound(new ApiError($"project {projectId} not found")) : StatusCode(201, dto);
	}

	[HttpGet("{projectId:int}/schedules")]
	public async Task<ActionResult<IReadOnlyList<AgentBoard.Application.Scheduling.Dtos.AgentScheduleDto>>> ProjectSchedules(
		int projectId, [FromQuery] int limit = 50, [FromQuery] int offset = 0, CancellationToken ct = default)
	{
		if (await Provider.GetProjectAsync(projectId, ct) is null)
			return NotFound(new ApiError($"project {projectId} not found"));
		return Ok(await Provider.ListProjectSchedulesAsync(projectId, Math.Clamp(limit, 1, 200), Math.Max(0, offset), ct));
	}

	[HttpPost("{projectId:int}/schedules")]
	public async Task<ActionResult<AgentBoard.Application.Scheduling.Dtos.AgentScheduleDto>> CreateProjectSchedule(
		int projectId, [FromBody] AgentBoard.Application.Board.Dtos.ProjectScheduleCreateRequest body, CancellationToken ct)
	{
		if (await Provider.GetProjectAsync(projectId, ct) is null)
			return NotFound(new ApiError($"project {projectId} not found"));
		var title = (body.Title ?? string.Empty).Trim();
		var scheduleType = (body.ScheduleType ?? string.Empty).Trim();
		if (title.Length == 0 || title.Length > 300)
			return UnprocessableEntity(new ApiError("title must be 1-300 characters"));
		if (scheduleType != "cron")
			return UnprocessableEntity(new ApiError("schedule_type must be cron"));
		if (string.IsNullOrWhiteSpace(body.CronExpr))
			return UnprocessableEntity(new ApiError("cron_expr is required for cron schedules"));

		var dto = await Provider.CreateProjectScheduleAsync(
			projectId, title, scheduleType, body.CronExpr, CurrentUser.UserId, ct);
		return dto is null ? NotFound(new ApiError($"project {projectId} not found")) : StatusCode(201, dto);
	}

	[HttpPost("{projectId:int}/sprints")]
	public async Task<ActionResult<AgentBoard.Application.Board.Dtos.SprintDto>> ProjectSprint(
		int projectId, [FromBody] AgentBoard.Application.Board.Dtos.SprintCreateRequest body, CancellationToken ct)
	{
		DateTime? start = DateTime.TryParse(body.StartDate, out var startValue) ? startValue : null;
		DateTime? end = DateTime.TryParse(body.EndDate, out var endValue) ? endValue : null;
		var dto = await Provider.CreateProjectSprintAsync(projectId, body.Title, body.Goal, start, end, ct);
		return dto is null ? NotFound(new ApiError($"project {projectId} not found")) : StatusCode(201, dto);
	}

	[HttpGet("{projectId:int}/export")]
	public async Task<ActionResult<AgentBoard.Application.Board.Dtos.ProjectExportDto>> Export(
		int projectId, CancellationToken ct)
	{
		var dto = await Provider.ExportProjectAsync(projectId, ct);
		return dto is null ? NotFound(new ApiError($"project {projectId} not found")) : Ok(dto);
	}

	[HttpPost("{projectId:int}/import")]
	public async Task<ActionResult<AgentBoard.Application.Board.Dtos.ProjectImportResult>> Import(
		int projectId, [FromBody] AgentBoard.Application.Board.Dtos.ProjectImportRequest? body, CancellationToken ct)
	{
		if (body is null)
		{
			if (await Provider.GetProjectAsync(projectId, ct) is null)
				return NotFound(new ApiError($"project {projectId} not found"));
			return UnprocessableEntity(new ApiError("request body is required"));
		}
		var dto = await Provider.ImportProjectAsync(projectId, body, ct);
		return dto is null ? NotFound(new ApiError($"project {projectId} not found")) : Ok(dto);
	}

	// ===================== P2b: project writes (mirrors FastAPI projects router) =====================

	[HttpPost]
	[ProducesResponseType(typeof(AgentBoard.Application.Board.Dtos.ProjectDto), 201)]
	[ProducesResponseType(typeof(ApiError), 422)]
	[ProducesResponseType(typeof(ApiError), 409)]
	public async Task<ActionResult<AgentBoard.Application.Board.Dtos.ProjectDto>> Create(
		[FromBody] AgentBoard.Application.Board.Dtos.ProjectCreateRequest body, CancellationToken ct)
	{
		var dto = await Provider.CreateProjectAsync(body.Name, body.Key, body.Description, CurrentUser.UserId, ct);
		return StatusCode(StatusCodes.Status201Created, dto);
	}

	[HttpPatch("{id:int}")]
	[ProducesResponseType(typeof(AgentBoard.Application.Board.Dtos.ProjectDto), 200)]
	[ProducesResponseType(typeof(ApiError), 404)]
	[ProducesResponseType(typeof(ApiError), 422)]
	[ProducesResponseType(typeof(ApiError), 409)]
	public async Task<ActionResult<AgentBoard.Application.Board.Dtos.ProjectDto>> Patch(
		int id, [FromBody] AgentBoard.Application.Board.Dtos.ProjectPatchRequest body, CancellationToken ct)
	{
		var dto = await Provider.UpdateProjectAsync(id, body.Name, body.Key, body.Description, body.IsPrivate, body.IsArchived, ct);
		return dto is null ? NotFound(new ApiError($"project {id} not found")) : Ok(dto);
	}

	[HttpDelete("{id:int}")]
	[ProducesResponseType(typeof(OkResult), 200)]
	[ProducesResponseType(typeof(ApiError), 404)]
	public async Task<ActionResult> Delete(int id, CancellationToken ct)
	{
		var ok = await Provider.DeleteProjectAsync(id, ct);
		return ok ? Ok(new { ok = true }) : NotFound(new ApiError($"project {id} not found"));
	}

	// ===================== P3: project extensions =====================

	[HttpGet("extended")]
	[ProducesResponseType(typeof(IReadOnlyList<AgentBoard.Application.Board.Dtos.ProjectDto>), 200)]
	public async Task<ActionResult<IReadOnlyList<AgentBoard.Application.Board.Dtos.ProjectDto>>> ListExtended(
		[FromQuery] int limit = 50, [FromQuery] int offset = 0,
		[FromQuery(Name = "include_archived")] bool includeArchived = false,
		CancellationToken ct = default)
	{
		return Ok(await Provider.ListProjectsExtendedAsync(limit, offset, includeArchived, CurrentUser.UserId, ct));
	}

	[HttpPost("{id:int}/archive")]
	[ProducesResponseType(typeof(AgentBoard.Application.Board.Dtos.ProjectArchiveResult), 200)]
	[ProducesResponseType(typeof(ApiError), 404)]
	public async Task<ActionResult<AgentBoard.Application.Board.Dtos.ProjectArchiveResult>> Archive(int id, CancellationToken ct)
	{
		var dto = await Provider.ArchiveProjectAsync(id, ct);
		return dto is null
			? NotFound(new ApiError($"project {id} not found"))
			: Ok(new AgentBoard.Application.Board.Dtos.ProjectArchiveResult(true, dto.IsArchived));
	}

	[HttpPost("{id:int}/unarchive")]
	[ProducesResponseType(typeof(AgentBoard.Application.Board.Dtos.ProjectArchiveResult), 200)]
	[ProducesResponseType(typeof(ApiError), 404)]
	public async Task<ActionResult<AgentBoard.Application.Board.Dtos.ProjectArchiveResult>> Unarchive(int id, CancellationToken ct)
	{
		var dto = await Provider.UnarchiveProjectAsync(id, ct);
		return dto is null
			? NotFound(new ApiError($"project {id} not found"))
			: Ok(new AgentBoard.Application.Board.Dtos.ProjectArchiveResult(true, dto.IsArchived));
	}

	[HttpPost("bulk-archive")]
	[ProducesResponseType(typeof(object), 200)]
	public async Task<ActionResult> BulkArchive([FromBody] AgentBoard.Application.Board.Dtos.ProjectIdsRequest? body, CancellationToken ct)
	{
		if (body?.Ids is null)
			return UnprocessableEntity(new ApiError("ids must be a list of integers"));
		var count = await Provider.BulkArchiveProjectsAsync(body.Ids.ToList(), ct);
		return Ok(new { ok = true, archived = count });
	}

	[HttpPost("bulk-unarchive")]
	[ProducesResponseType(typeof(object), 200)]
	public async Task<ActionResult> BulkUnarchive([FromBody] AgentBoard.Application.Board.Dtos.ProjectIdsRequest? body, CancellationToken ct)
	{
		if (body?.Ids is null)
			return UnprocessableEntity(new ApiError("ids must be a list of integers"));
		var count = await Provider.BulkUnarchiveProjectsAsync(body.Ids.ToList(), ct);
		return Ok(new { ok = true, unarchived = count });
	}

	[HttpGet("{id:int}/tickets")]
	[ProducesResponseType(typeof(AgentBoard.Application.Board.Dtos.TicketListResult), 200)]
	[ProducesResponseType(typeof(ApiError), 404)]
	public async Task<ActionResult<AgentBoard.Application.Board.Dtos.TicketListResult>> Tickets(
		int id,
		[FromQuery(Name = "status")] string status = "incomplete",
		[FromQuery] string sort = "created_at",
		[FromQuery] string order = "desc",
		[FromQuery] int limit = 200,
		[FromQuery] int offset = 0,
		CancellationToken ct = default)
	{
		var result = await Provider.ListProjectTicketsAsync(id, status, sort, order, Math.Clamp(limit, 1, 500), Math.Max(0, offset), ct);
		return Ok(result);
	}
}

// ===================== User projects endpoint =====================

/// <summary>User-scoped project listing.</summary>
[ApiController]
[Route("api/users")]
[Produces("application/json")]
public sealed class UserProjectsController : BaseController<IBoardProvider>
{
	public UserProjectsController(IBoardProvider provider, ICurrentUser current) : base(provider, current) { }

	[HttpGet("me/projects")]
	[ProducesResponseType(typeof(IReadOnlyList<AgentBoard.Application.Board.Dtos.ProjectDto>), 200)]
	[ProducesResponseType(typeof(ApiError), 401)]
	public async Task<ActionResult<IReadOnlyList<AgentBoard.Application.Board.Dtos.ProjectDto>>> MyProjects(
		[FromQuery] string? role = null,
		CancellationToken ct = default)
	{
		var uid = CurrentUser.UserId;
		if (uid is null) return Problem(401, "authentication required");
		return Ok(await Provider.ListUserProjectsAsync(uid.Value, role, ct));
	}
}
