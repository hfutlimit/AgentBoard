using System.Security.Cryptography;
using System.Text;
using AgentBoard.Api.Realtime;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.SignalR;

namespace AgentBoard.Api.Controllers;

[ApiController]
[Route("api/internal/realtime")]
[AllowAnonymous]
public sealed class RealtimeNotificationsController(
    IConfiguration configuration,
    IHubContext<ProposalHub> hub) : ControllerBase
{
    [HttpPost("proposals/questions")]
    public async Task<IActionResult> ProposalQuestions(
        [FromBody] ProposalQuestionRaisedNotification notification,
        CancellationToken cancellationToken)
    {
        var expected = configuration["AgentBoard:Realtime:InternalKey"]
            ?? Environment.GetEnvironmentVariable("AGENTBOARD_REALTIME_INTERNAL_KEY");
        var supplied = Request.Headers["X-AgentBoard-Realtime-Key"].ToString();
        if (string.IsNullOrWhiteSpace(expected))
            return StatusCode(StatusCodes.Status503ServiceUnavailable, new { detail = "realtime bridge is not configured" });
        if (string.IsNullOrEmpty(supplied) || !CryptographicOperations.FixedTimeEquals(
                Encoding.UTF8.GetBytes(expected), Encoding.UTF8.GetBytes(supplied)))
            return Unauthorized();

        await hub.Clients.All.SendAsync(
            ProposalHub.QuestionRaisedEvent,
            notification,
            cancellationToken);
        return Accepted(new { ok = true });
    }
}
