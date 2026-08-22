// SPDX-License-Identifier: MIT
using AgentBoard.Api.Api.Base;
using AgentBoard.Api.Api.Common;
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board;
using AgentBoard.Application.Board.Dtos;
using Microsoft.AspNetCore.Mvc;

namespace AgentBoard.Api.Features.Webhooks;

/// <summary>Webhook configuration. Mirrors FastAPI <c>/api/webhooks</c>.</summary>
[ApiController]
[Route("api/webhooks")]
[Produces("application/json")]
public sealed class WebhooksController : BaseController<IWebhookProvider>
{
    public WebhooksController(IWebhookProvider provider, ICurrentUser current) : base(provider, current) { }

    [HttpGet]
    [ProducesResponseType(typeof(IReadOnlyList<WebhookDto>), 200)]
    public async Task<ActionResult<IReadOnlyList<WebhookDto>>> List(
        [FromQuery(Name = "project_id")] int? projectId, CancellationToken ct) =>
        Ok(await Provider.ListWebhooksAsync(projectId, ct));

    [HttpPost]
    [ProducesResponseType(typeof(WebhookDto), 201)]
    [ProducesResponseType(typeof(ApiError), 422)]
    public async Task<ActionResult<WebhookDto>> Create(
        [FromBody] WebhookCreateRequest body, CancellationToken ct)
    {
        var dto = await Provider.CreateWebhookAsync(body.ProjectId, body.Name, body.Url, body.Events, CurrentUser.UserId, ct);
        return StatusCode(StatusCodes.Status201Created, dto);
    }

    [HttpPatch("{id:int}")]
    [ProducesResponseType(typeof(WebhookDto), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    [ProducesResponseType(typeof(ApiError), 422)]
    public async Task<ActionResult<WebhookDto>> Patch(
        int id, [FromBody] WebhookPatchRequest body, CancellationToken ct)
    {
        var dto = await Provider.UpdateWebhookAsync(id, body.Name, body.Url, body.Events, body.Enabled, ct);
        return dto is null ? NotFound(new ApiError($"webhook {id} not found")) : Ok(dto);
    }

    [HttpDelete("{id:int}")]
    [ProducesResponseType(typeof(object), 200)]
    [ProducesResponseType(typeof(ApiError), 404)]
    public async Task<ActionResult> Delete(int id, CancellationToken ct)
    {
        var ok = await Provider.DeleteWebhookAsync(id, ct);
        return ok ? Ok(new { ok = true }) : NotFound(new ApiError($"webhook {id} not found"));
    }
}
