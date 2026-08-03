using Microsoft.Data.Sqlite;
using Microsoft.Extensions.Options;

namespace AgentBoard.ProposalWorker;

public sealed class ExecutionStore
{
    private readonly string _connectionString;
    private readonly ILogger<ExecutionStore> _log;

    public ExecutionStore(IOptions<WorkerOptions> options, ILogger<ExecutionStore> log)
    {
        var path = Path.GetFullPath(options.Value.HistoryDatabasePath);
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        _connectionString = new SqliteConnectionStringBuilder { DataSource = path }.ToString();
        _log = log;
        Initialize();
    }

    private void Initialize()
    {
        using var connection = new SqliteConnection(_connectionString);
        connection.Open();
        using var command = connection.CreateCommand();
        command.CommandText = """
            CREATE TABLE IF NOT EXISTS executions (
              id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id INTEGER NOT NULL, round INTEGER NOT NULL,
              reason TEXT NOT NULL, source TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL,
              finished_at TEXT NULL, exit_code INTEGER NULL, output TEXT NOT NULL DEFAULT '', error TEXT NULL,
              payload TEXT NOT NULL, retry_requested INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS ix_executions_started_at ON executions(started_at DESC);
            """;
        command.ExecuteNonQuery();
    }

    public async Task<long> StartAsync(ProposalMessage message, string source, CancellationToken ct)
    {
        await using var connection = new SqliteConnection(_connectionString);
        await connection.OpenAsync(ct);
        await using var cmd = connection.CreateCommand();
        cmd.CommandText = "INSERT INTO executions(proposal_id,round,reason,source,status,started_at,payload) VALUES($id,$round,$reason,$source,'running',$at,$payload); SELECT last_insert_rowid();";
        cmd.Parameters.AddWithValue("$id", message.ProposalId); cmd.Parameters.AddWithValue("$round", message.Round);
        cmd.Parameters.AddWithValue("$reason", message.Reason); cmd.Parameters.AddWithValue("$source", source);
        cmd.Parameters.AddWithValue("$at", DateTimeOffset.UtcNow.ToString("O")); cmd.Parameters.AddWithValue("$payload", message.ToJson());
        return (long)(await cmd.ExecuteScalarAsync(ct) ?? 0L);
    }

    public async Task CompleteAsync(long id, int exitCode, string output, string? error, CancellationToken ct)
    {
        await using var connection = new SqliteConnection(_connectionString); await connection.OpenAsync(ct);
        await using var cmd = connection.CreateCommand();
        cmd.CommandText = "UPDATE executions SET status=$status,finished_at=$at,exit_code=$code,output=$output,error=$error WHERE id=$id";
        cmd.Parameters.AddWithValue("$status", exitCode == 0 ? "succeeded" : "failed"); cmd.Parameters.AddWithValue("$at", DateTimeOffset.UtcNow.ToString("O"));
        cmd.Parameters.AddWithValue("$code", exitCode); cmd.Parameters.AddWithValue("$output", output); cmd.Parameters.AddWithValue("$error", (object?)error ?? DBNull.Value); cmd.Parameters.AddWithValue("$id", id);
        await cmd.ExecuteNonQueryAsync(ct);
    }

    public async Task<IReadOnlyList<ExecutionRecord>> ListAsync(int limit)
    {
        var list = new List<ExecutionRecord>(); await using var c = new SqliteConnection(_connectionString); await c.OpenAsync();
        await using var cmd = c.CreateCommand(); cmd.CommandText = "SELECT id,proposal_id,round,reason,source,status,started_at,finished_at,exit_code,output,error,payload FROM executions ORDER BY id DESC LIMIT $limit"; cmd.Parameters.AddWithValue("$limit", limit);
        await using var rows = await cmd.ExecuteReaderAsync(); while (await rows.ReadAsync()) list.Add(Read(rows)); return list;
    }

    public async Task<ExecutionRecord?> GetAsync(long id)
    {
        await using var c = new SqliteConnection(_connectionString); await c.OpenAsync(); await using var cmd = c.CreateCommand();
        cmd.CommandText = "SELECT id,proposal_id,round,reason,source,status,started_at,finished_at,exit_code,output,error,payload FROM executions WHERE id=$id"; cmd.Parameters.AddWithValue("$id", id);
        await using var rows = await cmd.ExecuteReaderAsync(); return await rows.ReadAsync() ? Read(rows) : null;
    }

    public async Task<bool> QueueRetryAsync(long id)
    {
        await using var c = new SqliteConnection(_connectionString); await c.OpenAsync(); await using var cmd = c.CreateCommand();
        cmd.CommandText = "UPDATE executions SET retry_requested=1 WHERE id=$id AND status='failed'"; cmd.Parameters.AddWithValue("$id", id); return await cmd.ExecuteNonQueryAsync() == 1;
    }

    public async Task<IReadOnlyList<(long Id, ProposalMessage Message)>> GetRetryRequestsAsync()
    {
        var list = new List<(long, ProposalMessage)>(); await using var c = new SqliteConnection(_connectionString); await c.OpenAsync(); await using var cmd = c.CreateCommand();
        cmd.CommandText = "SELECT id,payload FROM executions WHERE retry_requested=1 AND status='failed' ORDER BY id";
        await using var rows = await cmd.ExecuteReaderAsync(); while (await rows.ReadAsync()) list.Add((rows.GetInt64(0), ProposalMessage.Parse(System.Text.Encoding.UTF8.GetBytes(rows.GetString(1))))); return list;
    }

    public async Task ClearRetryAsync(long id)
    {
        await using var c = new SqliteConnection(_connectionString); await c.OpenAsync(); await using var cmd = c.CreateCommand(); cmd.CommandText = "UPDATE executions SET retry_requested=0 WHERE id=$id"; cmd.Parameters.AddWithValue("$id", id); await cmd.ExecuteNonQueryAsync();
    }

    private static ExecutionRecord Read(SqliteDataReader r) => new(
        r.GetInt64(0), r.GetInt64(1), r.GetInt32(2), r.GetString(3), r.GetString(4), r.GetString(5),
        DateTimeOffset.Parse(r.GetString(6)), r.IsDBNull(7) ? null : DateTimeOffset.Parse(r.GetString(7)), r.IsDBNull(8) ? null : r.GetInt32(8),
        r.GetString(9), r.IsDBNull(10) ? null : r.GetString(10), r.GetString(11));
}
