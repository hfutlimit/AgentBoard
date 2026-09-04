// SPDX-License-Identifier: MIT
using System.Text.Json;
using AgentBoard.Contracts;
using System.Text.Json.Serialization;
using AgentBoard.Domain.Workflow.Durable;
using Microsoft.Data.Sqlite;

namespace AgentBoard.Infrastructure.Persistence.Workflow;

/// <summary>
/// Durable home for the A1 server plane (doc 150 NFR-001, NFR-005;
/// doc 151 §6.1: the authoritative state update and the outbox rows commit in
/// one database transaction).
/// </summary>
/// <remarks>
/// <para>
/// Components are stored as JSON rows under one table. What matters for the
/// A1 exit criteria is the commit boundary: a crash before <c>COMMIT</c> loses
/// both the state change and its outbox rows, so a redelivered message is
/// reprocessed from the last committed state rather than half-applied; a crash
/// after <c>COMMIT</c> leaves everything a restart needs to answer duplicates
/// and stale attempts.
/// </para>
/// <para>
/// Row-level journaling (one row per transition/outbox message) is a scale-up
/// for A4; the semantics tested here do not depend on the granularity.
/// </para>
/// </remarks>
public sealed class SqlitePlaneStore : IDisposable, IPlaneCommitter
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        Converters = { new JsonStringEnumConverter() },
    };

    private const string ComponentsTable = "plane_component";

    private readonly string _connectionString;

    public SqlitePlaneStore(string databasePath)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(databasePath);
        _connectionString = new SqliteConnectionStringBuilder
        {
            DataSource = databasePath,
            ForeignKeys = true,
        }.ToString();

        EnsureSchema();
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

    private void EnsureSchema()
    {
        using var connection = Open();
        using var command = connection.CreateCommand();
        command.CommandText = $"""
            CREATE TABLE IF NOT EXISTS {ComponentsTable} (
                kind       TEXT PRIMARY KEY,
                json       TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """;
        command.ExecuteNonQuery();
    }

    /// <summary>
    /// Commits the plane's complete durable state in one transaction: either
    /// every component is visible to a later process, or none is.
    /// </summary>
    public void Commit(DurableServerPlane plane)
    {
        ArgumentNullException.ThrowIfNull(plane);
        CommitState(plane.Capture());
    }

    /// <summary>Domain-facing seam: commit a captured snapshot durably.</summary>
    public void Commit(PlaneState state) => CommitState(state);

    public void CommitState(PlaneState state)
    {
        using var connection = Open();
        using var transaction = connection.BeginTransaction();

        try
        {
            Write(connection, transaction, "registry", state.Registry);
            Write(connection, transaction, "outbox", state.Outbox);
            Write(connection, transaction, "inbox", state.Inbox);
            Write(connection, transaction, "leases", state.Assignments);
            Write(connection, transaction, "dead_letters", state.DeadLetters);
            Write(connection, transaction, "approvals", state.Approvals);
            Write(connection, transaction, "sent_commands", state.SentCommands);
            Write(connection, transaction, "pending_retries", state.PendingRetries);
            Write(connection, transaction, "handoffs", state.Handoffs);
            Write(connection, transaction, "evidence", state.Evidence);
            Write(connection, transaction, "orchestration", state.Orchestration);
            Write(connection, transaction, "task_status_projections",
                state.TaskStatusProjections ?? Array.Empty<TaskStatusProjection>());
            transaction.Commit();
        }
        catch
        {
            transaction.Rollback();
            throw;
        }
    }

    /// <summary>
    /// The commit boundary for "update the registry and queue the follow-up
    /// command together": <paramref name="work"/> mutates the in-memory plane,
    /// and only its commit makes both halves durable (doc 151 §5.6 step 4).
    /// </summary>
    public void Commit(DurableServerPlane plane, Action work)
    {
        ArgumentNullException.ThrowIfNull(plane);
        ArgumentNullException.ThrowIfNull(work);
        plane.CommitAtomic(this, work);
    }

    /// <summary>Loads persisted state, or null when nothing was ever committed.</summary>
    public PlaneState? Load()
    {
        using var connection = Open();

        var registry = Read<RegistryState>(connection, "registry");
        if (registry is null)
        {
            return null;
        }

        return new PlaneState(
            registry,
            Read<IReadOnlyList<OutboxMessage>>(connection, "outbox") ?? Array.Empty<OutboxMessage>(),
            Read<IReadOnlyList<DedupEntry>>(connection, "inbox") ?? Array.Empty<DedupEntry>(),
            Read<IReadOnlyList<Assignment>>(connection, "leases") ?? Array.Empty<Assignment>(),
            Read<IReadOnlyList<DeadLetterEntry>>(connection, "dead_letters") ?? Array.Empty<DeadLetterEntry>(),
            Read<IReadOnlyList<ApprovalRequest>>(connection, "approvals") ?? Array.Empty<ApprovalRequest>(),
            Read<IReadOnlyList<CommandEnvelope>>(connection, "sent_commands") ?? Array.Empty<CommandEnvelope>(),
            Read<IReadOnlyList<PendingRetry>>(connection, "pending_retries") ?? Array.Empty<PendingRetry>(),
            Read<IReadOnlyList<HandoffContext>>(connection, "handoffs") ?? Array.Empty<HandoffContext>(),
            Read<IReadOnlyList<AttemptEvidence>>(connection, "evidence") ?? Array.Empty<AttemptEvidence>(),
            Read<WorkflowOrchestrationState>(connection, "orchestration")
                ?? new WorkflowOrchestrationState(
                    Array.Empty<WorkflowRunContextState>(), Array.Empty<WorkflowStagePlan>()),
            Read<IReadOnlyList<TaskStatusProjection>>(connection, "task_status_projections")
                ?? Array.Empty<TaskStatusProjection>());

        // Contracts records need no converter beyond string enums for the
        // status/category members.
        static T? Read<T>(SqliteConnection connection, string kind)
        {
            using var command = connection.CreateCommand();
            command.CommandText = $"SELECT json FROM {ComponentsTable} WHERE kind = $kind";
            command.Parameters.AddWithValue("$kind", kind);

            var value = command.ExecuteScalar() as string;
            return value is null ? default : JsonSerializer.Deserialize<T>(value, JsonOptions);
        }
    }

    /// <summary>True when a full commit exists (recovery has something to restore).</summary>
    public bool HasDurableState()
    {
        using var connection = Open();
        using var command = connection.CreateCommand();
        command.CommandText = $"SELECT COUNT(*) FROM {ComponentsTable} WHERE kind = 'registry'";
        return Convert.ToInt64(command.ExecuteScalar()!) > 0;
    }

    private static void Write<T>(SqliteConnection connection, SqliteTransaction transaction, string kind, T value)
    {
        using var command = connection.CreateCommand();
        command.Transaction = transaction;
        command.CommandText = $"""
            INSERT INTO {ComponentsTable} (kind, json, updated_at)
            VALUES ($kind, $json, $now)
            ON CONFLICT(kind) DO UPDATE SET json = excluded.json, updated_at = excluded.updated_at;
            """;
        command.Parameters.AddWithValue("$kind", kind);
        command.Parameters.AddWithValue("$json", JsonSerializer.Serialize(value, JsonOptions));
        command.Parameters.AddWithValue("$now", DateTimeOffset.UtcNow.ToString("O"));
        command.ExecuteNonQuery();
    }

    public void Dispose()
    {
        SqliteConnection.ClearAllPools();
    }
}
