// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Base;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Overview;

/// <summary>Cross-project dashboard aggregate. Mirrors FastAPI <c>/api/overview</c>.</summary>
[ApiController]
[Route("api/overview")]
[Produces("application/json")]
public sealed class OverviewController : BaseController<IBoardProvider>
{
    public OverviewController(IBoardProvider provider, ICurrentUser current) : base(provider, current) { }

    [HttpGet]
    [ProducesResponseType(typeof(AgentBoard.Application.Board.Dtos.OverviewDto), 200)]
    public async Task<ActionResult<AgentBoard.Application.Board.Dtos.OverviewDto>> Get(CancellationToken ct) =>
        // Anonymous callers (no bearer) get an empty aggregate — mirrors FastAPI's
        // _optional_user_id returning None. Authenticated callers see their scope.
        Ok(await Provider.GetOverviewAsync(CurrentUser.UserId, CurrentUser.IsAdmin, ct));
}
