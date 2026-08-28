// SPDX-License-Identifier: MIT
namespace AgentBoard.ProposalWorker;

/// <summary>
/// Resolved worker identity. Built once at startup, then read-only; the single
/// source of truth for every consumer (WorkerState, RabbitMqConsumerService,
/// WorkerHeartbeatService) so they cannot drift (#7 in the 2026-08-28 review:
/// when <c>appsettings.json</c> set <c>Worker.Id=""</c>, WorkerState fell back
/// to the machine name while the Rabbit queue and heartbeat payload reported
/// the empty string, so the server routed work to a queue the worker was
/// not listening on).
/// </summary>
public sealed class WorkerIdentity
{
    public string WorkerId { get; }
    public string ResolvedFrom { get; }

    public WorkerIdentity(Microsoft.Extensions.Options.IOptions<WorkerOptions> options)
    {
        var raw = options.Value.Id;
        if (!string.IsNullOrWhiteSpace(raw))
        {
            WorkerId = raw.Trim();
            ResolvedFrom = "config";
        }
        else
        {
            // Fall back to machine name so health, queue, and heartbeat all
            // report the same non-empty identifier. We never want an empty
            // worker_id to leak into /health, RabbitMQ queue names, or
            // heartbeat payloads because the server uses it as a routing key.
            WorkerId = Environment.MachineName;
            ResolvedFrom = "machine-fallback";
        }
    }
}
