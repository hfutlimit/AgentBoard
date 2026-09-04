// SPDX-License-Identifier: MIT
using System.Text.Json;
using AgentBoard.Contracts;
using Microsoft.Data.Sqlite;

namespace AgentBoard.Node.Durable;

/// <summary>
/// SQLite homes for the three Node durable components named by doc 154 A2:
/// the command journal, the local event store, and the result outbox log.
/// A1's review cycle kept flagging these as "in-memory prototypes"; with a
/// real store the atomicity arguments stop being conventions and become
/// transactions the database enforces.
/// </summary>
/// <remarks>
/// One file, three tables: the Node's durable footprint is small and
/// single-writer. Connections open per call — SQLite's own locking arbitrates,
/// and WAL keeps readers off the writer. Every mutation that must be atomic
/// (the journal's two dedup keys) runs inside one <c>BEGIN IMMEDIATE</c>.
/// </remarks>
internal static class NodeStoreJson
{
    internal static readonly JsonSerializerOptions Options = new()
    {
        Converters = { new System.Text.Json.Serialization.JsonStringEnumConverter() },
    };
}

public sealed class SqliteNodeCommandJournal : INodeCommandJournal, IDisposable
{
    private readonly string _connectionString;

    public SqliteNodeCommandJournal(string databasePath)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(databasePath);
        _connectionString = new SqliteConnectionStringBuilder { DataSource = databasePath }.ToString();

        using var connection = Open();
        using var command = connection.CreateCommand();
        command.CommandText = """
            CREATE TABLE IF NOT EXISTS node_journal (
                dedup_key   TEXT PRIMARY KEY,
                message_id  TEXT NOT NULL,
                command_json TEXT NOT NULL,
                accepted_at TEXT NOT NULL
            );
            """;
        command.ExecuteNonQuery();
    }

    private SqliteConnection Open()
    {
        var connection = new SqliteConnection(_connectionString);
        connection.Open();

        using var pragma = connection.CreateCommand();
        pragma.CommandText = "PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;";
        pragma.ExecuteNonQuery();
        return connection;
    }

    /// <summary>
    /// Both keys are checked and written inside ONE transaction: "the message
    /// key landed but the business key did not" is not a state SQLite can
    /// exhibit, which is what makes redelivery-after-crash safe even if the
    /// process dies mid-accept (doc 151 §6.1).
    /// </summary>
    public JournalAttempt TryAccept(CommandEnvelope command, string messageKey, string businessKey)
    {
        using var connection = Open();
        using var transaction = connection.BeginTransaction(System.Data.IsolationLevel.Serializable);

        try
        {
            using (var check = connection.CreateCommand())
            {
                check.Transaction = transaction;
                check.CommandText = "SELECT 1 FROM node_journal WHERE dedup_key = $a OR dedup_key = $b LIMIT 2";
                check.Parameters.AddWithValue("$a", messageKey);
                check.Parameters.AddWithValue("$b", businessKey);

                using var reader = check.ExecuteReader();
                if (reader.Read())
                {
                    transaction.Commit();
                    return JournalAttempt.Duplicate;
                }
            }

            var json = JsonSerializer.Serialize(command, NodeStoreJson.Options);
            Insert(connection, transaction, messageKey, command.MessageId, json);
            Insert(connection, transaction, businessKey, command.MessageId, json);

            transaction.Commit();
            return JournalAttempt.Accepted;
        }
        catch
        {
            transaction.Rollback();
            throw;
        }
    }

    private static void Insert(SqliteConnection connection, SqliteTransaction transaction, string key, string messageId, string json)
    {
        using var insert = connection.CreateCommand();
        insert.Transaction = transaction;
        insert.CommandText = "INSERT INTO node_journal (dedup_key, message_id, command_json, accepted_at) VALUES ($k, $m, $j, $t)";
        insert.Parameters.AddWithValue("$k", key);
        insert.Parameters.AddWithValue("$m", messageId);
        insert.Parameters.AddWithValue("$j", json);
        insert.Parameters.AddWithValue("$t", DateTimeOffset.UtcNow.ToString("O"));
        insert.ExecuteNonQuery();
    }

    /// <summary>Journal replay for restart: one row per message id, in arrival order.</summary>
    public IReadOnlyList<CommandEnvelope> All()
    {
        using var connection = Open();
        using var select = connection.CreateCommand();
        select.CommandText = "SELECT command_json FROM node_journal GROUP BY message_id ORDER BY MIN(rowid)";

        var results = new List<CommandEnvelope>();
        using var reader = select.ExecuteReader();
        while (reader.Read())
        {
            var value = reader.GetString(0);
            results.Add(JsonSerializer.Deserialize<CommandEnvelope>(value, NodeStoreJson.Options)!);
        }

        return results;
    }

    public void Dispose() => SqliteConnection.ClearAllPools();
}

public sealed class SqliteEventSink : IEventSink, IDisposable
{
    private readonly string _connectionString;

    public SqliteEventSink(string databasePath)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(databasePath);
        _connectionString = new SqliteConnectionStringBuilder { DataSource = databasePath }.ToString();

        using var connection = Open();
        using var command = connection.CreateCommand();
        command.CommandText = """
            CREATE TABLE IF NOT EXISTS node_events (
                dedup_key  TEXT PRIMARY KEY,
                subject    TEXT NOT NULL,
                event_json TEXT NOT NULL,
                stored_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_node_events_subject ON node_events (subject);
            """;
        command.ExecuteNonQuery();
    }

    private SqliteConnection Open()
    {
        var connection = new SqliteConnection(_connectionString);
        connection.Open();

        using var pragma = connection.CreateCommand();
        pragma.CommandText = "PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;";
        pragma.ExecuteNonQuery();
        return connection;
    }

    /// <summary>INSERT OR IGNORE gives the source+event_id dedup a unique index behind it.</summary>
    public bool TryInsert(EventEnvelope envelope)
    {
        using var connection = Open();
        using var insert = connection.CreateCommand();
        insert.CommandText = "INSERT OR IGNORE INTO node_events (dedup_key, subject, event_json, stored_at) VALUES ($k, $s, $j, $t)";
        insert.Parameters.AddWithValue("$k", envelope.DedupKey);
        insert.Parameters.AddWithValue("$s", envelope.Subject);
        insert.Parameters.AddWithValue("$j", JsonSerializer.Serialize(envelope, NodeStoreJson.Options));
        insert.Parameters.AddWithValue("$t", envelope.Time.ToString("O"));
        return insert.ExecuteNonQuery() > 0;
    }

    public EventEnvelope? Find(string dedupKey)
    {
        using var connection = Open();
        using var select = connection.CreateCommand();
        select.CommandText = "SELECT event_json FROM node_events WHERE dedup_key = $k";
        select.Parameters.AddWithValue("$k", dedupKey);

        var value = select.ExecuteScalar() as string;
        return value is null ? null : JsonSerializer.Deserialize<EventEnvelope>(value, NodeStoreJson.Options);
    }

    public IReadOnlyList<EventEnvelope> All()
    {
        using var connection = Open();
        using var select = connection.CreateCommand();
        select.CommandText = "SELECT event_json FROM node_events ORDER BY rowid";

        var results = new List<EventEnvelope>();
        using var reader = select.ExecuteReader();
        while (reader.Read())
        {
            results.Add(JsonSerializer.Deserialize<EventEnvelope>(reader.GetString(0), NodeStoreJson.Options)!);
        }

        return results;
    }

    public int Count()
    {
        using var connection = Open();
        using var select = connection.CreateCommand();
        select.CommandText = "SELECT COUNT(*) FROM node_events";
        return Convert.ToInt32(select.ExecuteScalar()!);
    }

    public void Dispose() => SqliteConnection.ClearAllPools();
}

public sealed class SqliteResultOutboxLog : IResultOutboxLog, IDisposable
{
    private readonly string _connectionString;

    public SqliteResultOutboxLog(string databasePath)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(databasePath);
        _connectionString = new SqliteConnectionStringBuilder { DataSource = databasePath }.ToString();

        using var connection = Open();
        using var command = connection.CreateCommand();
        command.CommandText = """
            CREATE TABLE IF NOT EXISTS node_result_outbox (
                message_id   TEXT PRIMARY KEY,
                record_json  TEXT NOT NULL,
                state        INTEGER NOT NULL,
                updated_at   TEXT NOT NULL
            );
            """;
        command.ExecuteNonQuery();
    }

    private SqliteConnection Open()
    {
        var connection = new SqliteConnection(_connectionString);
        connection.Open();

        using var pragma = connection.CreateCommand();
        pragma.CommandText = "PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;";
        pragma.ExecuteNonQuery();
        return connection;
    }

    public void Save(LocalOutboxRecord record)
    {
        using var connection = Open();
        using var upsert = connection.CreateCommand();
        upsert.CommandText = """
            INSERT INTO node_result_outbox (message_id, record_json, state, updated_at)
            VALUES ($m, $j, $s, $t)
            ON CONFLICT(message_id) DO UPDATE SET record_json = excluded.record_json,
                                                  state = excluded.state,
                                                  updated_at = excluded.updated_at;
            """;
        upsert.Parameters.AddWithValue("$m", record.MessageId);
        upsert.Parameters.AddWithValue("$j", JsonSerializer.Serialize(record, NodeStoreJson.Options));
        upsert.Parameters.AddWithValue("$s", (int)record.State);
        upsert.Parameters.AddWithValue("$t", DateTimeOffset.UtcNow.ToString("O"));
        upsert.ExecuteNonQuery();
    }

    public IReadOnlyList<LocalOutboxRecord> LoadAll()
    {
        using var connection = Open();
        using var select = connection.CreateCommand();
        select.CommandText = "SELECT record_json FROM node_result_outbox ORDER BY rowid";

        var results = new List<LocalOutboxRecord>();
        using var reader = select.ExecuteReader();
        while (reader.Read())
        {
            results.Add(JsonSerializer.Deserialize<LocalOutboxRecord>(reader.GetString(0), NodeStoreJson.Options)!);
        }

        return results;
    }

    public void Dispose() => SqliteConnection.ClearAllPools();
}
