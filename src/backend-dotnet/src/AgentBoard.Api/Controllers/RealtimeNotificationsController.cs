// SPDX-License-Identifier: MIT
using System.Net;
using System.Security.Cryptography;
using System.Text;
using AgentBoard.Api.Realtime;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.SignalR;

namespace AgentBoard.Api.Controllers;

/// <summary>
/// Internal bridge endpoint: FastAPI calls this when a proposal question
/// round finishes and the goal-mode UI should re-fetch the proposal.
/// P1-5: broadcast now goes to a per-project SignalR group rather than
/// <c>Clients.All</c>, so browsers connected to other projects do not
/// receive (and waste cycles refetching) foreign proposal metadata.
/// P1-6: the endpoint enforces two checks — a shared key compared in
/// constant time, and an IP allowlist (default: loopback + the docker
/// compose service network) so a leaked key alone cannot let random
/// Internet hosts forge notifications.
/// </summary>
[ApiController]
[Route("api/internal/realtime")]
[AllowAnonymous]   // shared key + IP allowlist are enforced below; keep off the public auth chain
public sealed class RealtimeNotificationsController(
    IConfiguration configuration,
    IHubContext<ProposalHub> hub,
    IWebHostEnvironment env) : ControllerBase
{
    [HttpPost("proposals/questions")]
    public async Task<IActionResult> ProposalQuestions(
        [FromBody] ProposalQuestionRaisedNotification notification,
        CancellationToken cancellationToken)
    {
        // P1-6 (IP allowlist): even if the shared key leaks, only callers
        // from the configured CIDR can reach the endpoint. In dev/test we
        // accept loopback by default; in docker compose the api container
        // reaches api-dotnet via the bridge network so we also accept
        // private 172.16.0.0/12 and 10.0.0.0/8. Operators can override via
        // AgentBoard:Realtime:TrustedProxies.
        if (!IsCallerAllowed(configuration, env, HttpContext.Connection.RemoteIpAddress))
            return StatusCode(StatusCodes.Status403Forbidden,
                new { detail = "realtime bridge: caller IP not on allowlist" });

        var expected = configuration["AgentBoard:Realtime:InternalKey"]
            ?? Environment.GetEnvironmentVariable("AGENTBOARD_REALTIME_INTERNAL_KEY");
        if (string.IsNullOrWhiteSpace(expected))
            // 404 instead of 503 to avoid leaking the "service exists but
            // is unconfigured" state to random probers.
            return NotFound();
        var supplied = Request.Headers["X-AgentBoard-Realtime-Key"].ToString();
        if (string.IsNullOrEmpty(supplied) || !CryptographicOperations.FixedTimeEquals(
                Encoding.UTF8.GetBytes(expected), Encoding.UTF8.GetBytes(supplied)))
            return Unauthorized();

        // P1-5: route the notification to the project's group only. Clients
        // that did not opt in via ProposalHub.JoinProject never receive it.
        await hub.Clients
            .Group(ProposalHub.ProjectGroup(notification.ProjectId))
            .SendAsync(ProposalHub.QuestionRaisedEvent, notification, cancellationToken);
        return Accepted(new { ok = true });
    }

    private static bool IsCallerAllowed(
        IConfiguration config,
        IWebHostEnvironment env,
        IPAddress? remote)
    {
        if (remote is null) return false;
        // Always allow loopback in dev/test so docker host + host-mapped
        // ports keep working without extra configuration.
        if (IPAddress.IsLoopback(remote)) return true;

        // Default trusted proxies cover RFC1918 private space, which lets
        // docker compose (172.16-172.31), docker desktop (10.0.0.0/8 host
        // networks), and corporate dev hosts (192.168.x.x) reach the bridge
        // without further configuration. Operators can override / extend by
        // setting AgentBoard:Realtime:TrustedProxies.
        var section = config.GetSection("AgentBoard:Realtime:TrustedProxies").Get<string[]>()
            ?? new[]
            {
                "10.0.0.0/8",
                "172.16.0.0/12",
                "192.168.0.0/16",
            };
        foreach (var entry in section)
        {
            if (NetworkRange.TryParse(entry, out var range) && range.Contains(remote))
                return true;
        }
        return false;
    }

    /// <summary>Parses a CIDR or single IP into a <see cref="NetworkRange"/>.</summary>
    private readonly struct NetworkRange
    {
        private readonly IPAddress _base;
        private readonly int _prefix;
        private NetworkRange(IPAddress @base, int prefix) { _base = @base; _prefix = prefix; }
        public bool Contains(IPAddress addr) => NetworkRangeContains(_base, _prefix, addr);
        public static bool TryParse(string text, out NetworkRange range)
        {
            range = default;
            if (string.IsNullOrWhiteSpace(text)) return false;
            text = text.Trim();
            var slash = text.IndexOf('/');
            try
            {
                if (slash < 0)
                {
                    if (!IPAddress.TryParse(text, out var ip)) return false;
                    range = new NetworkRange(ip, ip.AddressFamily == System.Net.Sockets.AddressFamily.InterNetwork ? 32 : 128);
                    return true;
                }
                var ipPart = text[..slash];
                var prefixPart = text[(slash + 1)..];
                if (!IPAddress.TryParse(ipPart, out var ip2)) return false;
                if (!int.TryParse(prefixPart, out var prefix)) return false;
                var max = ip2.AddressFamily == System.Net.Sockets.AddressFamily.InterNetwork ? 32 : 128;
                if (prefix < 0 || prefix > max) return false;
                range = new NetworkRange(ip2, prefix);
                return true;
            }
            catch
            {
                return false;
            }
        }
        private static bool NetworkRangeContains(IPAddress @base, int prefix, IPAddress addr)
        {
            if (@base.AddressFamily != addr.AddressFamily) return false;
            var baseBytes = @base.GetAddressBytes();
            var addrBytes = addr.GetAddressBytes();
            int full = prefix / 8, rem = prefix % 8;
            for (int i = 0; i < full; i++)
                if (baseBytes[i] != addrBytes[i]) return false;
            if (rem == 0) return true;
            int mask = 0xFF & (0xFF << (8 - rem));
            return (baseBytes[full] & mask) == (addrBytes[full] & mask);
        }
    }
}
