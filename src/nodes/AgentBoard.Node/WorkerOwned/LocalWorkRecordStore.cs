// SPDX-License-Identifier: MIT
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using Microsoft.Data.Sqlite;

namespace AgentBoard.Node.WorkerOwned;

public static class LocalWorkRecordStates
{
    public const string Running = "running";
    public const string Pending = "result_pending_delivery";
    public const string Succeeded = "succeeded";
    public const string Failed = "failed";
    public const string Interrupted = "interrupted";
    public static readonly string[] All = [Running, Pending, Succeeded, Failed, Interrupted];
}

public sealed record LocalWorkRecordStart(long WorkId, string ClaimToken, string AgentId,
    string Provider, string Model, string WorkKind, string BusinessItem);
public sealed record LocalWorkRecordSummary(string RecordId, long WorkId, string AgentId, string WorkKind,
    string BusinessItem, string State, string DeliveryState, DateTimeOffset StartedAt,
    DateTimeOffset? EndedAt, string? Summary);
public sealed record LocalWorkRecordEvent(int Sequence, DateTimeOffset OccurredAt, string State,
    string DeliveryState, string Code);
public sealed record LocalWorkRecordDetail(LocalWorkRecordSummary Summary, string Provider,
    string Model, string? ResultDetail, string? FailureCode, bool Retryable,
    DateTimeOffset? DeliveredAt, IReadOnlyList<LocalWorkRecordEvent> Events);
public sealed record LocalWorkRecordPage(IReadOnlyList<LocalWorkRecordSummary> Items, string? NextCursor);

/// <summary>
/// Worker-owned display history.  This intentionally has no dependency on the
/// Journal's raw result and never exposes a token or provider output verbatim.
/// </summary>
public sealed class LocalWorkRecordStore
{
    private readonly string _connectionString;
    private readonly string _scope;
    private readonly byte[] _cursorKey;

    public LocalWorkRecordStore(string databasePath, string scope)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(scope);
        _scope = scope;
        var path = Path.GetFullPath(databasePath);
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        _connectionString = new SqliteConnectionStringBuilder { DataSource = path, DefaultTimeout = 1 }.ToString();
        _cursorKey = Initialize();
    }

    private byte[] Initialize()
    {
        using var connection = Open();
        using var command = connection.CreateCommand();
        command.CommandText = "PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;";
        command.ExecuteNonQuery();
        command.CommandText = """
            CREATE TABLE IF NOT EXISTS worker_owned_work_record_identity(
              singleton INTEGER PRIMARY KEY CHECK(singleton=1), scope TEXT NOT NULL, cursor_key BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS worker_owned_work_records(
              record_id TEXT PRIMARY KEY, scope TEXT NOT NULL, work_id INTEGER NOT NULL, token_fingerprint TEXT NOT NULL,
              agent_id TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, work_kind TEXT NOT NULL,
              business_item TEXT NOT NULL, state TEXT NOT NULL, delivery_state TEXT NOT NULL,
              started_at TEXT NOT NULL, ended_at TEXT NULL, delivered_at TEXT NULL, updated_at TEXT NOT NULL,
              result_summary TEXT NULL, result_detail TEXT NULL, failure_code TEXT NULL, retryable INTEGER NOT NULL DEFAULT 0,
              UNIQUE(scope, work_id, token_fingerprint));
            CREATE INDEX IF NOT EXISTS ix_worker_owned_records_agent ON worker_owned_work_records(scope, agent_id, started_at DESC, record_id DESC);
            CREATE INDEX IF NOT EXISTS ix_worker_owned_records_state ON worker_owned_work_records(scope, state, started_at DESC, record_id DESC);
            CREATE TABLE IF NOT EXISTS worker_owned_work_record_events(
              scope TEXT NOT NULL, record_id TEXT NOT NULL, sequence INTEGER NOT NULL, occurred_at TEXT NOT NULL,
              state TEXT NOT NULL, delivery_state TEXT NOT NULL, event_code TEXT NOT NULL,
              PRIMARY KEY(scope, record_id, sequence));
            """;
        command.ExecuteNonQuery();
        using var tx = connection.BeginTransaction();
        command.Transaction = tx;
        command.CommandText = "SELECT scope, cursor_key FROM worker_owned_work_record_identity WHERE singleton=1";
        using var reader = command.ExecuteReader();
        if (reader.Read())
        {
            if (!StringComparer.Ordinal.Equals(reader.GetString(0), _scope))
                throw new InvalidOperationException("Worker work history belongs to another Server or Worker; use a separate history path");
            var key = (byte[])reader[1];
            tx.Commit();
            return key;
        }
        reader.Close();
        var keyNew = RandomNumberGenerator.GetBytes(32);
        command.CommandText = "INSERT INTO worker_owned_work_record_identity(singleton,scope,cursor_key) VALUES(1,$scope,$key)";
        command.Parameters.AddWithValue("$scope", _scope);
        command.Parameters.AddWithValue("$key", keyNew);
        command.ExecuteNonQuery();
        tx.Commit();
        return keyNew;
    }

    public string CreateRunning(LocalWorkRecordStart start)
    {
        var fingerprint = Fingerprint(start.ClaimToken);
        using var connection = Open(); using var tx = connection.BeginTransaction(); using var command = connection.CreateCommand(); command.Transaction = tx;
        command.CommandText = "SELECT record_id FROM worker_owned_work_records WHERE scope=$scope AND work_id=$work AND token_fingerprint=$token";
        command.Parameters.AddWithValue("$scope", _scope); command.Parameters.AddWithValue("$work", start.WorkId); command.Parameters.AddWithValue("$token", fingerprint);
        var existing = command.ExecuteScalar() as string;
        if (existing is not null) { tx.Commit(); return existing; }
        var id = Guid.NewGuid().ToString("N"); var now = DateTimeOffset.UtcNow;
        command.Parameters.Clear();
        command.CommandText = """
            INSERT INTO worker_owned_work_records(record_id,scope,work_id,token_fingerprint,agent_id,provider,model,work_kind,business_item,state,delivery_state,started_at,updated_at)
            VALUES($id,$scope,$work,$token,$agent,$provider,$model,$kind,$item,$state,'not_applicable',$now,$now)
            """;
        command.Parameters.AddWithValue("$id", id); command.Parameters.AddWithValue("$scope", _scope); command.Parameters.AddWithValue("$work", start.WorkId);
        command.Parameters.AddWithValue("$token", fingerprint); command.Parameters.AddWithValue("$agent", start.AgentId); command.Parameters.AddWithValue("$provider", start.Provider);
        command.Parameters.AddWithValue("$model", start.Model); command.Parameters.AddWithValue("$kind", start.WorkKind); command.Parameters.AddWithValue("$item", WorkRecordRedactor.Clean(start.BusinessItem, 200));
        command.Parameters.AddWithValue("$state", LocalWorkRecordStates.Running); command.Parameters.AddWithValue("$now", now.ToString("O")); command.ExecuteNonQuery();
        Append(command, id, 1, now, LocalWorkRecordStates.Running, "not_applicable", "created"); tx.Commit(); return id;
    }

    public void MarkPending(string id, WorkRecordProjection projection)
    {
        // A process may persist the journal result immediately before it dies.
        // Startup truthfully marks the display attempt interrupted; a later
        // live-fence replay can then record its known pending-delivery fact
        // without calling the provider again.
        Transition(id, [LocalWorkRecordStates.Running, LocalWorkRecordStates.Interrupted], LocalWorkRecordStates.Pending,
            "pending", "journal_result_saved", projection.Summary, projection.Detail, null, false, null);
    }
    public void MarkSucceeded(string id) => Transition(id, [LocalWorkRecordStates.Pending], LocalWorkRecordStates.Succeeded,
        "confirmed", "completion_confirmed", null, null, null, false, DateTimeOffset.UtcNow);
    public void MarkFailed(string id, string code, bool retryable = false) => Transition(id,
        [LocalWorkRecordStates.Running, LocalWorkRecordStates.Pending, LocalWorkRecordStates.Interrupted], LocalWorkRecordStates.Failed,
        "not_applicable", "failed", null, null, WorkRecordProjection.FailureCode(code), retryable, null);

    /// <summary>
    /// Records a terminal fact learned from the fenced work API after a local
    /// success write failed.  It deliberately identifies the attempt by the
    /// journal token fingerprint, never by work id alone, and never creates a
    /// record.  This makes restart/replay reconciliation presentation-only and
    /// cannot trigger a provider call.
    /// </summary>
    public bool ReconcileTerminal(long workId, string claimToken, string terminalState)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(claimToken);
        if (terminalState is not (LocalWorkRecordStates.Succeeded or LocalWorkRecordStates.Failed))
            throw new ArgumentException("Invalid terminal work state", nameof(terminalState));

        using var connection = Open();
        using var tx = connection.BeginTransaction();
        using var command = connection.CreateCommand();
        command.Transaction = tx;
        command.CommandText = "SELECT record_id, state FROM worker_owned_work_records WHERE scope=$scope AND work_id=$work AND token_fingerprint=$token";
        command.Parameters.AddWithValue("$scope", _scope);
        command.Parameters.AddWithValue("$work", workId);
        command.Parameters.AddWithValue("$token", Fingerprint(claimToken));
        using var reader = command.ExecuteReader();
        if (!reader.Read()) { tx.Commit(); return false; }
        var id = reader.GetString(0);
        var current = reader.GetString(1);
        reader.Close();

        if (current is LocalWorkRecordStates.Succeeded or LocalWorkRecordStates.Failed)
        {
            tx.Commit();
            return false;
        }

        if (terminalState == LocalWorkRecordStates.Succeeded)
        {
            // A completed result is only known for an already-persisted result
            // attempt.  A bare running attempt is recovered honestly instead.
            if (current is not (LocalWorkRecordStates.Pending or LocalWorkRecordStates.Interrupted))
            {
                tx.Commit();
                return false;
            }
            Transition(connection, tx, id, [LocalWorkRecordStates.Pending, LocalWorkRecordStates.Interrupted],
                LocalWorkRecordStates.Succeeded, "confirmed", "completion_reconciled", null, null, null, false, DateTimeOffset.UtcNow);
        }
        else
        {
            Transition(connection, tx, id, [LocalWorkRecordStates.Running, LocalWorkRecordStates.Pending, LocalWorkRecordStates.Interrupted],
                LocalWorkRecordStates.Failed, "not_applicable", "failure_reconciled", null, null, "CompletionRejected", false, null);
        }
        tx.Commit();
        return true;
    }
    public void RecoverInterrupted() => BulkInterrupt();

    private void BulkInterrupt()
    {
        using var connection = Open(); using var tx = connection.BeginTransaction(); using var cmd = connection.CreateCommand(); cmd.Transaction = tx;
        cmd.CommandText = "SELECT record_id FROM worker_owned_work_records WHERE scope=$scope AND state='running'"; cmd.Parameters.AddWithValue("$scope", _scope);
        var ids = new List<string>(); using (var reader = cmd.ExecuteReader()) while (reader.Read()) ids.Add(reader.GetString(0));
        foreach (var id in ids) Transition(connection, tx, id, [LocalWorkRecordStates.Running], LocalWorkRecordStates.Interrupted, "not_applicable", "recovered_interrupted", null, null, null, false, null);
        tx.Commit();
    }

    private void Transition(string id, IReadOnlyCollection<string> expected, string state, string delivery, string code, string? summary, string? detail, string? failure, bool retryable, DateTimeOffset? delivered)
    {
        using var connection = Open(); using var tx = connection.BeginTransaction();
        Transition(connection, tx, id, expected, state, delivery, code, summary, detail, failure, retryable, delivered); tx.Commit();
    }
    private void Transition(SqliteConnection connection, SqliteTransaction tx, string id, IReadOnlyCollection<string> expected, string state, string delivery, string code, string? summary, string? detail, string? failure, bool retryable, DateTimeOffset? delivered)
    {
        using var cmd = connection.CreateCommand(); cmd.Transaction = tx;
        cmd.CommandText = "SELECT state, delivery_state, sequence FROM worker_owned_work_records r JOIN (SELECT record_id, MAX(sequence) sequence FROM worker_owned_work_record_events WHERE scope=$scope GROUP BY record_id) e ON e.record_id=r.record_id WHERE r.scope=$scope AND r.record_id=$id";
        cmd.Parameters.AddWithValue("$scope", _scope); cmd.Parameters.AddWithValue("$id", id); using var reader = cmd.ExecuteReader();
        if (!reader.Read()) throw new KeyNotFoundException("Unknown local work record");
        var current = reader.GetString(0); var currentDelivery = reader.GetString(1); var sequence = reader.GetInt32(2); reader.Close();
        if (current == state && currentDelivery == delivery) return;
        if (!expected.Contains(current)) throw new InvalidOperationException("Invalid local work record state transition");
        var now = DateTimeOffset.UtcNow;
        cmd.Parameters.Clear();
        cmd.CommandText = "UPDATE worker_owned_work_records SET state=$state,delivery_state=$delivery,updated_at=$now,ended_at=CASE WHEN $terminal=1 THEN $now ELSE ended_at END,delivered_at=COALESCE($delivered,delivered_at),result_summary=COALESCE($summary,result_summary),result_detail=COALESCE($detail,result_detail),failure_code=COALESCE($failure,failure_code),retryable=$retryable WHERE scope=$scope AND record_id=$id";
        cmd.Parameters.AddWithValue("$state", state); cmd.Parameters.AddWithValue("$delivery", delivery); cmd.Parameters.AddWithValue("$now", now.ToString("O")); cmd.Parameters.AddWithValue("$terminal", state is LocalWorkRecordStates.Succeeded or LocalWorkRecordStates.Failed or LocalWorkRecordStates.Interrupted ? 1 : 0);
        cmd.Parameters.AddWithValue("$delivered", (object?)delivered?.ToString("O") ?? DBNull.Value); cmd.Parameters.AddWithValue("$summary", (object?)summary ?? DBNull.Value); cmd.Parameters.AddWithValue("$detail", (object?)detail ?? DBNull.Value); cmd.Parameters.AddWithValue("$failure", (object?)failure ?? DBNull.Value); cmd.Parameters.AddWithValue("$retryable", retryable ? 1 : 0); cmd.Parameters.AddWithValue("$scope", _scope); cmd.Parameters.AddWithValue("$id", id); cmd.ExecuteNonQuery();
        Append(cmd, id, sequence + 1, now, state, delivery, code);
    }
    private void Append(SqliteCommand cmd, string id, int sequence, DateTimeOffset now, string state, string delivery, string code)
    {
        cmd.Parameters.Clear(); cmd.CommandText = "INSERT INTO worker_owned_work_record_events(scope,record_id,sequence,occurred_at,state,delivery_state,event_code) VALUES($scope,$id,$seq,$at,$state,$delivery,$code)";
        cmd.Parameters.AddWithValue("$scope", _scope); cmd.Parameters.AddWithValue("$id", id); cmd.Parameters.AddWithValue("$seq", sequence);
        cmd.Parameters.AddWithValue("$at", now.ToString("O")); cmd.Parameters.AddWithValue("$state", state); cmd.Parameters.AddWithValue("$delivery", delivery); cmd.Parameters.AddWithValue("$code", code); cmd.ExecuteNonQuery();
    }

    public LocalWorkRecordPage List(string agentId, int pageSize, string? state, string? cursor)
    {
        if (pageSize is < 1 or > 100 || (state is not null && !LocalWorkRecordStates.All.Contains(state, StringComparer.Ordinal))) throw new ArgumentException("Invalid history paging parameter");
        (string At, string Id)? position = cursor is null ? null : DecodeCursor(cursor, agentId, state);
        using var connection = Open(); using var cmd = connection.CreateCommand();
        cmd.CommandText = """
            SELECT record_id,work_id,agent_id,work_kind,business_item,state,delivery_state,started_at,ended_at,result_summary,failure_code
            FROM worker_owned_work_records WHERE scope=$scope AND agent_id=$agent
            AND ($state IS NULL OR state=$state)
            AND ($at IS NULL OR started_at<$at OR (started_at=$at AND record_id<$id))
            ORDER BY started_at DESC,record_id DESC LIMIT $limit
            """;
        cmd.Parameters.AddWithValue("$scope", _scope); cmd.Parameters.AddWithValue("$agent", agentId); cmd.Parameters.AddWithValue("$state", (object?)state ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$at", (object?)position?.At ?? DBNull.Value); cmd.Parameters.AddWithValue("$id", (object?)position?.Id ?? DBNull.Value); cmd.Parameters.AddWithValue("$limit", pageSize + 1);
        var items = new List<LocalWorkRecordSummary>(); using var reader = cmd.ExecuteReader();
        while (reader.Read()) items.Add(ReadSummary(reader));
        var more = items.Count > pageSize; if (more) items.RemoveAt(items.Count - 1);
        var next = more ? EncodeCursor(agentId, state, items[^1]) : null;
        return new(items, next);
    }

    public LocalWorkRecordDetail? Get(string id, string agentId)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(agentId);
        using var connection = Open(); using var cmd = connection.CreateCommand();
        cmd.CommandText = "SELECT record_id,work_id,agent_id,work_kind,business_item,state,delivery_state,started_at,ended_at,result_summary,failure_code,provider,model,result_detail,retryable,delivered_at FROM worker_owned_work_records WHERE scope=$scope AND record_id=$id AND agent_id=$agent";
        cmd.Parameters.AddWithValue("$scope", _scope); cmd.Parameters.AddWithValue("$id", id); cmd.Parameters.AddWithValue("$agent", agentId); using var reader = cmd.ExecuteReader(); if (!reader.Read()) return null;
        var summary = ReadSummary(reader); var failure = reader.IsDBNull(10) ? null : WorkRecordRedactor.Clean(reader.GetString(10), 200); var provider = reader.GetString(11); var model = reader.GetString(12); var detail = reader.IsDBNull(13) ? null : WorkRecordRedactor.Clean(reader.GetString(13), 8192); var retryable = reader.GetInt32(14) != 0;
        DateTimeOffset? delivered = reader.IsDBNull(15) ? null : DateTimeOffset.Parse(reader.GetString(15)); reader.Close();
        cmd.Parameters.Clear(); cmd.CommandText = "SELECT sequence,occurred_at,state,delivery_state,event_code FROM worker_owned_work_record_events WHERE scope=$scope AND record_id=$id ORDER BY sequence"; cmd.Parameters.AddWithValue("$scope", _scope); cmd.Parameters.AddWithValue("$id", id);
        var events = new List<LocalWorkRecordEvent>(); using var eventsReader = cmd.ExecuteReader(); while (eventsReader.Read()) events.Add(new(eventsReader.GetInt32(0), DateTimeOffset.Parse(eventsReader.GetString(1)), eventsReader.GetString(2), eventsReader.GetString(3), eventsReader.GetString(4)));
        return new(summary, provider, model, detail, failure, retryable, delivered, events);
    }

    private static LocalWorkRecordSummary ReadSummary(SqliteDataReader reader) => new(reader.GetString(0), reader.GetInt64(1), reader.GetString(2), reader.GetString(3), WorkRecordRedactor.Clean(reader.GetString(4), 200), reader.GetString(5), reader.GetString(6), DateTimeOffset.Parse(reader.GetString(7)), reader.IsDBNull(8) ? null : DateTimeOffset.Parse(reader.GetString(8)), WorkRecordRedactor.Clean(reader.IsDBNull(9) ? reader.IsDBNull(10) ? "" : reader.GetString(10) : reader.GetString(9), 500));
    private SqliteConnection Open() { var c = new SqliteConnection(_connectionString); c.Open(); return c; }
    private static string Fingerprint(string token) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(token)));
    private string EncodeCursor(string agent, string? state, LocalWorkRecordSummary item)
    {
        var payload = $"{_scope}\n{agent}\n{state ?? ""}\n{item.StartedAt:O}\n{item.RecordId}"; var bytes = Encoding.UTF8.GetBytes(payload); var sig = HMACSHA256.HashData(_cursorKey, bytes);
        return Convert.ToBase64String(bytes.Concat(sig).ToArray()).TrimEnd('=').Replace('+', '-').Replace('/', '_');
    }
    private (string At, string Id) DecodeCursor(string cursor, string agent, string? state)
    {
        if (cursor.Length > 512) throw new ArgumentException("Invalid history cursor");
        try { var text = cursor.Replace('-', '+').Replace('_', '/'); text += new string('=', (4 - text.Length % 4) % 4); var all = Convert.FromBase64String(text); if (all.Length <= 32) throw new FormatException(); var bytes = all[..^32]; if (!CryptographicOperations.FixedTimeEquals(HMACSHA256.HashData(_cursorKey, bytes), all[^32..])) throw new FormatException(); var parts = Encoding.UTF8.GetString(bytes).Split('\n'); if (parts.Length != 5 || parts[0] != _scope || parts[1] != agent || parts[2] != (state ?? "") || !DateTimeOffset.TryParse(parts[3], out _)) throw new FormatException(); return (parts[3], parts[4]); }
        catch (Exception e) when (e is FormatException or ArgumentException) { throw new ArgumentException("Invalid history cursor"); }
    }
}

public sealed record WorkRecordProjection(string Summary, string Detail)
{
    public static WorkRecordProjection From(string kind, JsonObject output)
    {
        string value = kind switch
        {
            "dev" => Commit(output),
            "design" => DesignResult(output),
            "proposal" => ProposalDecision(output),
            "qa" => QaResult(output),
            "design_review" or "dev_review" or "qa_review" => Decision(output),
            _ => "recorded"
        };
        return new($"Structured {kind} result: {value}", $"Result: {value}");
    }
    private static string? StringValue(JsonObject output, string name) => output[name] is JsonValue value && value.TryGetValue<string>(out var text) ? text : null;
    private static bool? BoolValue(JsonObject output, string name) => output[name] is JsonValue value && value.TryGetValue<bool>(out var flag) ? flag : null;
    private static string Commit(JsonObject output) => StringValue(output, "commit") is { } commit && (commit.Length is 40 or 64) && commit.All(Uri.IsHexDigit) ? commit : "recorded";
    private static string DesignResult(JsonObject output)
    {
        var commit = Commit(output);
        return output["design_document_id"] is JsonValue value
               && ((value.TryGetValue<long>(out var documentId) && documentId > 0)
                   || (value.TryGetValue<int>(out var documentId32) && documentId32 > 0))
            ? $"{commit}; document: {(value.TryGetValue<long>(out var id) ? id : value.GetValue<int>())}"
            : commit;
    }
    private static string ProposalDecision(JsonObject output)
    {
        var decision = StringValue(output, "decision");
        if (decision is not ("ask" or "finalize")) return "recorded";
        return BoolValue(output, "create_ticket") is { } create ? $"{decision}; create ticket: {create}" : decision;
    }
    private static string Decision(JsonObject output)
    {
        var decision = StringValue(output, "decision");
        if (decision is not ("approve" or "discuss" or "respond" or "confirm" or "withdraw" or "escalate")) return "recorded";
        // Discussion replies may expose their controlled position, but never
        // the review text, evidence, or free-form discussion payload.
        var position = StringValue(output, "position");
        return decision == "respond" && position is "agree" or "disagree" or "clarify"
            ? $"respond; position: {position}"
            : decision;
    }
    private static string QaResult(JsonObject output)
    {
        var result = BoolValue(output, "tests_passed") is bool passed ? passed ? "passed" : "failed" : "recorded";
        // The raw defect objects contain descriptions and evidence. Keep only
        // their bounded cardinality in the local display projection.
        var count = output["defects"] is JsonArray defects ? Math.Min(defects.Count, 100) : 0;
        return $"{result}; {count} defects";
    }
    public static string FailureCode(string code) => code is "ProviderFailed" or "OutputInvalid" or "JournalSaveFailed" or "HistoryWriteFailed" or "CompletionTransport" or "CompletionRejected" or "LeaseLost" or "Cancelled" ? code : "Unknown";
}

public static class WorkRecordRedactor
{
    public static string Clean(string value, int limit)
    {
        if (string.IsNullOrEmpty(value)) return value;
        var cleaned = System.Text.RegularExpressions.Regex.Replace(value, "(?i)(bearer\\s+|token[=:]\\s*|secret[=:]\\s*)[^\\s,;]+", "$1[redacted]");
        // A long opaque credential may not have a helpful key name.  Keep the
        // short, controlled projection values usable while removing values
        // which look like a copied API key, JWT, or fenced token.
        cleaned = System.Text.RegularExpressions.Regex.Replace(cleaned, "(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{32,}(?![A-Za-z0-9_-])",
            match => match.Value.Length is 40 or 64 && match.Value.All(Uri.IsHexDigit) ? match.Value : "[redacted]");
        cleaned = System.Text.RegularExpressions.Regex.Replace(cleaned, "(?<!\\w)(?:[A-Za-z]:\\\\|\\\\\\\\|/)(?:[^\\s<>\\\"']+)", "[path]");
        cleaned = new string(cleaned.Select(c => char.IsControl(c) && c is not '\r' and not '\n' and not '\t' ? ' ' : c).ToArray());
        return cleaned.Length <= limit ? cleaned : cleaned[..limit];
    }
}
