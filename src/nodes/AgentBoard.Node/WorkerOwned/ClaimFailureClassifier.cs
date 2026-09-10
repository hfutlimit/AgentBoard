// SPDX-License-Identifier: MIT
using System.Net;
using System.Text.Json.Nodes;

namespace AgentBoard.Node.WorkerOwned;

/// <summary>
/// Classifies a claim HTTP response into a structured outcome. Replaces the
/// previous substring-match approach (commit fdfe9ad) so the Node ↔ Server
/// error contract is read through a structured JSON parser, with a substring
/// fallback kept for safety against any future non-JSON response shapes.
///
/// (Not a strict DTO deserialize: we use <c>JsonNode</c> for forward
/// compatibility with extra envelope fields. Switch to <c>JsonSerializer
/// .Deserialize&lt;ErrorEnvelope&gt;</c> once the Server schema is frozen.)
///
/// The decision tree the delivery loop actually drives:
/// <list type="bullet">
///   <item><see cref="Kind.AckAndDrop"/>  — remove the local journal entry and ack
///     the delivery. Returned for <c>ghost_work_row</c> (row cannot be claimed by
///     any Agent), <c>new_token_required</c> (lease expired; the Server has
///     already moved on), and any 404 (row gone from durable store). Do NOT call
///     ReturnToTail; returning false here would loop the same unclaimable row
///     back into the queue and starve later work.</item>
///   <item><see cref="Kind.Forbidden"/>   — try the next candidate Agent.</item>
///   <item><see cref="Kind.Conflict"/>    — terminal reconciliation needed:
///     <c>work_failed</c>, lease conflict, or claim-lost-after-race. The caller
///     falls through to ReadTerminalResponse + status endpoint fallback.</item>
///   <item><see cref="Kind.Unexpected"/>  — bubble up via EnsureSuccessStatusCode.</item>
/// </list>
/// </summary>
internal static class ClaimFailureClassifier
{
    public enum Kind { AckAndDrop, Forbidden, Conflict, Unexpected }

    public readonly record struct Outcome(Kind Kind, string Reason, string? Detail);

    /// <summary>
    /// Stable identifier for the machine-readable Server reason. Mirrors
    /// FastAPI's <c>resolve_claim</c> contract. Keep this list in sync with
    /// <c>src/backend-fastapi/.../worker_work.py</c>.
    /// </summary>
    public static class Reasons
    {
        public const string GhostWorkRow = "ghost_work_row";
        public const string NewTokenRequired = "new_token_required";
        public const string WorkFailed = "work_failed";
    }

    public static Outcome Classify(HttpStatusCode status, string body)
    {
        var parsed = TryParseDetailReason(body);

        if (status == HttpStatusCode.NotFound)
        {
            // The Server has no record of this work_id (admin cleanup, age-out,
            // or post-replay). Requeuing cannot help; drop the delivery.
            return new Outcome(Kind.AckAndDrop, "row not found on server", body);
        }
        if (status == HttpStatusCode.Forbidden)
        {
            return new Outcome(Kind.Forbidden,
                string.IsNullOrEmpty(parsed)
                    ? ClassifyForbiddenSubstring(body)
                    : parsed,
                body);
        }
        if (status == HttpStatusCode.Conflict)
        {
            // Ack-and-drop reasons first; the caller has nothing left to do
            // and any retry would just re-publish the same doomed row.
            if (string.Equals(parsed, Reasons.GhostWorkRow, StringComparison.Ordinal))
            {
                return new Outcome(Kind.AckAndDrop, parsed, body);
            }
            if (string.Equals(parsed, Reasons.NewTokenRequired, StringComparison.Ordinal))
            {
                return new Outcome(Kind.AckAndDrop, parsed, body);
            }
            // Other 409s (work_failed, lease conflict, terminal) are reconciled
            // by the caller's ReadTerminalResponse + status endpoint fallback.
            return new Outcome(Kind.Conflict,
                string.IsNullOrEmpty(parsed) ? "conflict" : parsed, body);
        }
        return new Outcome(Kind.Unexpected, $"status {(int)status}", body);
    }

    /// <summary>
    /// Extracts <c>detail.reason</c> from a FastAPI error envelope. The current
    /// schema is <c>{"detail": {"reason": "...", ...contextual fields...}}</c>;
    /// older or non-conforming responses fall back to substring matching so we
    /// never silently lose a known failure mode.
    /// </summary>
    internal static string TryParseDetailReason(string body)
    {
        if (string.IsNullOrEmpty(body)) return "";
        try
        {
            var node = JsonNode.Parse(body);
            var detail = node?["detail"];
            if (detail is JsonObject obj)
            {
                return obj["reason"]?.GetValue<string>() ?? "";
            }
            if (detail is JsonValue val)
            {
                return val.GetValue<string>() ?? "";
            }
        }
        catch
        {
            // Not JSON; fall through to substring fallback below.
        }
        // Substring fallback — guarantees forward/backward compat even if the
        // Server returns a plain string or a different envelope shape.
        if (body.Contains(Reasons.GhostWorkRow, StringComparison.Ordinal)) return Reasons.GhostWorkRow;
        if (body.Contains(Reasons.NewTokenRequired, StringComparison.Ordinal)) return Reasons.NewTokenRequired;
        if (body.Contains(Reasons.WorkFailed, StringComparison.Ordinal)) return Reasons.WorkFailed;
        return "";
    }

    /// <summary>
    /// Legacy Forbidden bucket — older 403 messages used free-form English text
    /// instead of <c>detail.reason</c>. Kept so a Server that hasn't migrated
    /// still surfaces a useful label in logs.
    /// </summary>
    private static string ClassifyForbiddenSubstring(string body)
    {
        if (body.Contains("work owner does not match", StringComparison.Ordinal)) return "owner mismatch";
        if (body.Contains("independent reviewer/QA", StringComparison.Ordinal)) return "independent Agent required";
        if (body.Contains("Agent must belong", StringComparison.Ordinal)) return "Agent identity mismatch";
        if (body.Contains("original participant", StringComparison.Ordinal)) return "original participant required";
        return "forbidden";
    }
}
