// SPDX-License-Identifier: MIT
using Microsoft.Data.Sqlite;

namespace AgentBoard.Node.WorkerOwned;

public sealed record JournalEntry(long WorkId, string AgentId, string Token, string? Result);

/// <summary>Persist claim identity before HTTP, then result before completion/ACK.</summary>
public sealed class WorkJournal
{
    private readonly string _connectionString;
    public WorkJournal(string databasePath, string scope)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(scope);
        var full = Path.GetFullPath(databasePath);
        Directory.CreateDirectory(Path.GetDirectoryName(full)!);
        _connectionString = new SqliteConnectionStringBuilder { DataSource = full }.ToString();
        using var connection = Open();
        using var command = connection.CreateCommand();
        command.CommandText = """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS worker_owned_journal (
                work_id INTEGER PRIMARY KEY, agent_id TEXT NOT NULL,
                token TEXT NOT NULL, result TEXT NULL);
            CREATE TABLE IF NOT EXISTS worker_owned_identity (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), scope TEXT NOT NULL);
            """;
        command.ExecuteNonQuery();
        {
            using var transaction = connection.BeginTransaction();
            command.Transaction = transaction;
            command.CommandText = "SELECT scope FROM worker_owned_identity WHERE singleton=1";
            var existing = command.ExecuteScalar() as string;
            if (existing is null)
            {
                command.CommandText = "SELECT COUNT(*) FROM worker_owned_journal";
                if (Convert.ToInt64(command.ExecuteScalar()) != 0)
                    throw new InvalidOperationException("Unbound Worker journal contains executions; retain it and use a new journal path");
                command.CommandText = "INSERT INTO worker_owned_identity(singleton,scope) VALUES(1,$scope)";
                command.Parameters.AddWithValue("$scope", scope);
                command.ExecuteNonQuery();
            }
            else if (!StringComparer.Ordinal.Equals(existing, scope))
                throw new InvalidOperationException("Worker journal belongs to another Server or Worker; use a separate journal path");
            transaction.Commit();
        }
    }

    private SqliteConnection Open()
    {
        var connection = new SqliteConnection(_connectionString);
        connection.Open();
        return connection;
    }

    public JournalEntry? Get(long id)
    {
        using var connection = Open();
        using var command = connection.CreateCommand();
        command.CommandText = "SELECT agent_id, token, result FROM worker_owned_journal WHERE work_id=$id";
        command.Parameters.AddWithValue("$id", id);
        using var reader = command.ExecuteReader();
        return reader.Read() ? new(id, reader.GetString(0), reader.GetString(1), reader.IsDBNull(2) ? null : reader.GetString(2)) : null;
    }

    public void Save(JournalEntry entry)
    {
        using var connection = Open();
        using var command = connection.CreateCommand();
        command.CommandText = """
            INSERT INTO worker_owned_journal(work_id,agent_id,token,result) VALUES($id,$agent,$token,$result)
            ON CONFLICT(work_id) DO UPDATE SET agent_id=excluded.agent_id,token=excluded.token,result=excluded.result
            """;
        command.Parameters.AddWithValue("$id", entry.WorkId);
        command.Parameters.AddWithValue("$agent", entry.AgentId);
        command.Parameters.AddWithValue("$token", entry.Token);
        command.Parameters.AddWithValue("$result", (object?)entry.Result ?? DBNull.Value);
        command.ExecuteNonQuery();
    }

    public void Remove(long id)
    {
        using var connection = Open();
        using var command = connection.CreateCommand();
        command.CommandText = "DELETE FROM worker_owned_journal WHERE work_id=$id";
        command.Parameters.AddWithValue("$id", id);
        command.ExecuteNonQuery();
    }
}
