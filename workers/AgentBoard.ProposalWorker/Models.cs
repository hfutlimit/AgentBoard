using System.Text.Json;

namespace AgentBoard.ProposalWorker;

public sealed record ProposalMessage(long ProposalId, int Round, string Reason, string Timestamp)
{
    public static ProposalMessage Parse(ReadOnlyMemory<byte> body)
    {
        using var doc = JsonDocument.Parse(body);
        var root = doc.RootElement;
        if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty("proposal_id", out var id) ||
            !id.TryGetInt64(out var proposalId) || proposalId <= 0)
            throw new InvalidDataException("proposal message requires positive proposal_id");
        var round = root.TryGetProperty("round", out var r) && r.TryGetInt32(out var value) ? Math.Max(0, value) : 0;
        var reason = root.TryGetProperty("reason", out var why) ? why.GetString() ?? "" : "";
        var timestamp = root.TryGetProperty("ts", out var ts) ? ts.GetString() ?? "" : "";
        return new ProposalMessage(proposalId, round, reason, timestamp);
    }

    public string ToJson() => JsonSerializer.Serialize(new { proposal_id = ProposalId, round = Round, reason = Reason, ts = Timestamp });
}

public sealed record ExecutionRecord(
    long Id, long ProposalId, int Round, string Reason, string Source, string Status,
    DateTimeOffset StartedAt, DateTimeOffset? FinishedAt, int? ExitCode, string Output, string? Error, string Payload);
