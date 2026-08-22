// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Base;
using AgentBoard.Api.Api.Common;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board;
using AgentBoard.Application.Board.Dtos;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Search;

/// <summary>Global search. Mirrors FastAPI <c>/api/search</c>.</summary>
[ApiController]
[Route("api/search")]
[Produces("application/json")]
public sealed class SearchController : BaseController<ISearchProvider>
{
    public SearchController(ISearchProvider provider, ICurrentUser current) : base(provider, current) { }

    [HttpGet("stories")]
    [ProducesResponseType(typeof(IReadOnlyList<SearchResultItem>), 200)]
    public async Task<ActionResult<IReadOnlyList<SearchResultItem>>> SearchStories(
        [FromQuery] string? q, [FromQuery(Name = "project_id")] int? projectId,
        [FromQuery] int limit = 20, CancellationToken ct = default) =>
        Ok(await Provider.SearchStoriesAsync(q, projectId, limit, ct));

    [HttpGet("epics")]
    [ProducesResponseType(typeof(IReadOnlyList<SearchResultItem>), 200)]
    public async Task<ActionResult<IReadOnlyList<SearchResultItem>>> SearchEpics(
        [FromQuery] string? q, [FromQuery(Name = "project_id")] int? projectId,
        [FromQuery] int limit = 20, CancellationToken ct = default) =>
        Ok(await Provider.SearchEpicsAsync(q, projectId, limit, ct));

    [HttpGet("sprints")]
    [ProducesResponseType(typeof(IReadOnlyList<SearchResultItem>), 200)]
    public async Task<ActionResult<IReadOnlyList<SearchResultItem>>> SearchSprints(
        [FromQuery] string? q, [FromQuery(Name = "project_id")] int? projectId,
        [FromQuery] int limit = 20, CancellationToken ct = default) =>
        Ok(await Provider.SearchSprintsAsync(q, projectId, limit, ct));

    [HttpGet("agents")]
    [ProducesResponseType(typeof(IReadOnlyList<SearchResultItem>), 200)]
    public async Task<ActionResult<IReadOnlyList<SearchResultItem>>> SearchAgents(
        [FromQuery] string? q, [FromQuery] int limit = 20, CancellationToken ct = default) =>
        Ok(await Provider.SearchAgentsAsync(q, limit, ct));

    [HttpGet("notifications")]
    [ProducesResponseType(typeof(IReadOnlyList<SearchResultItem>), 200)]
    [ProducesResponseType(typeof(ApiError), 401)]
    public async Task<ActionResult<IReadOnlyList<SearchResultItem>>> SearchNotifications(
        [FromQuery] string? q, [FromQuery] int limit = 20, CancellationToken ct = default)
    {
        var uid = CurrentUser.UserId;
        if (uid is null) return Problem(StatusCodes.Status401Unauthorized, "authentication required");
        return Ok(await Provider.SearchNotificationsAsync(q, uid.Value, limit, ct));
    }

    [HttpGet("proposals")]
    [ProducesResponseType(typeof(IReadOnlyList<SearchResultItem>), 200)]
    public async Task<ActionResult<IReadOnlyList<SearchResultItem>>> SearchProposals(
        [FromQuery] string? q, [FromQuery] int limit = 20, CancellationToken ct = default) =>
        Ok(await Provider.SearchProposalsAsync(q, CurrentUser.UserId, limit, ct));

    [HttpGet("tickets")]
    [ProducesResponseType(typeof(IReadOnlyList<SearchResultItem>), 200)]
    public async Task<ActionResult<IReadOnlyList<SearchResultItem>>> SearchTickets(
        [FromQuery] string? q, [FromQuery(Name = "project_id")] int? projectId,
        [FromQuery] int limit = 20, CancellationToken ct = default) =>
        Ok(await Provider.SearchTicketsAsync(q, projectId, limit, ct));

    [HttpGet("schedules")]
    [ProducesResponseType(typeof(IReadOnlyList<SearchResultItem>), 200)]
    public async Task<ActionResult<IReadOnlyList<SearchResultItem>>> SearchSchedules(
        [FromQuery] string? q, [FromQuery(Name = "project_id")] int? projectId,
        [FromQuery] int limit = 20, CancellationToken ct = default) =>
        Ok(await Provider.SearchSchedulesAsync(q, projectId, limit, ct));

    [HttpGet("runs")]
    [ProducesResponseType(typeof(IReadOnlyList<SearchResultItem>), 200)]
    public async Task<ActionResult<IReadOnlyList<SearchResultItem>>> SearchRuns(
        [FromQuery] string? q, [FromQuery(Name = "project_id")] int? projectId,
        [FromQuery] int limit = 20, CancellationToken ct = default) =>
        Ok(await Provider.SearchRunsAsync(q, projectId, limit, ct));
}
