using System.Text.Json;
using AgentBoard.ProposalWorker;
using AgentBoard.ProposalWorker.Tests.Fixtures;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Xunit;

namespace AgentBoard.ProposalWorker.Tests;

/// <summary>
/// Sprint 6: WorkerState multi-agent + execution_logs + heartbeat split.
/// Pure in-memory (WorkerState) + SQLite (logs). No real processes.
/// </summary>
public sealed class Sprint6_WorkerStateTests : IDisposable
{
    private readonly TempDbFixture _fx = new();
    private long _nextId = 6000;
    private long _nextExecId = 1;

    public void Dispose() => _fx.Dispose();

    private ExecutionRequest Req(string agent = "workbuddy") => new(
        ExecutionKey: $"proposal:{_nextId++}:0:{agent}",
        WorkloadType: "proposal",
        WorkloadId: _nextId - 1,
        AgentType: agent,
        Round: 0,
        Source: "test",
        PayloadJson: "{}");

    // -------------------------------------------------------------------------
    // PerAgentCounters — three agents in flight → independent counters
    // -------------------------------------------------------------------------

    [Fact]
    public void Snapshot_reports_per_agent_running_counters_independently()
    {
        var state = NewState();

        BeginAndCount(state, "workbuddy");
        BeginAndCount(state, "minimax");
        BeginAndCount(state, "codex");

        var doc = SnapshotJson(state, new[] { "workbuddy", "minimax", "codex" });
        Assert.Equal(1, doc.GetProperty("agents").GetProperty("workbuddy").GetProperty("running").GetInt32());
        Assert.Equal(1, doc.GetProperty("agents").GetProperty("minimax").GetProperty("running").GetInt32());
        Assert.Equal(1, doc.GetProperty("agents").GetProperty("codex").GetProperty("running").GetInt32());

        Assert.Equal(1, doc.GetProperty("agents").GetProperty("workbuddy").GetProperty("total_executions").GetInt32());
        Assert.Equal(1, doc.GetProperty("agents").GetProperty("minimax").GetProperty("total_executions").GetInt32());
        Assert.Equal(1, doc.GetProperty("agents").GetProperty("codex").GetProperty("total_executions").GetInt32());
    }

    [Fact]
    public void Begin_then_End_decrements_running()
    {
        var state = NewState();
        var active = BeginAndCount(state, "workbuddy");

        var doc1 = SnapshotJson(state, new[] { "workbuddy" });
        Assert.Equal(1, doc1.GetProperty("agents").GetProperty("workbuddy").GetProperty("running").GetInt32());

        state.End(active);

        var doc2 = SnapshotJson(state, new[] { "workbuddy" });
        Assert.Equal(0, doc2.GetProperty("agents").GetProperty("workbuddy").GetProperty("running").GetInt32());
    }

    [Fact]
    public void End_clamps_running_at_zero_no_negative()
    {
        var state = NewState();
        var active = BeginAndCount(state, "workbuddy");
        state.End(active);
        state.End(active);  // double End — should be safe

        var doc = SnapshotJson(state, new[] { "workbuddy" });
        Assert.Equal(0, doc.GetProperty("agents").GetProperty("workbuddy").GetProperty("running").GetInt32());
    }

    // -------------------------------------------------------------------------
    // ActiveExecution lifecycle
    // -------------------------------------------------------------------------

    [Fact]
    public void Active_executions_listed_in_snapshot_with_agent_type()
    {
        var state = NewState();
        BeginAndCount(state, "workbuddy", workloadId: 100);
        BeginAndCount(state, "minimax", workloadId: 200);

        var doc = SnapshotJson(state, new[] { "workbuddy", "minimax" });
        var active = doc.GetProperty("active_executions");
        Assert.Equal(2, active.GetArrayLength());

        // Both agent types should appear in the active list.
        var types = active.EnumerateArray()
            .Select(a => a.GetProperty("agent_type").GetString())
            .ToHashSet();
        Assert.Contains("workbuddy", types);
        Assert.Contains("minimax", types);
    }

    [Fact]
    public void Active_executions_cleared_after_End()
    {
        var state = NewState();
        var a1 = BeginAndCount(state, "workbuddy", workloadId: 1);
        var a2 = BeginAndCount(state, "workbuddy", workloadId: 2);

        Assert.Equal(2, state.ActiveCount);

        state.End(a1);
        Assert.Equal(1, state.ActiveCount);

        state.End(a2);
        Assert.Equal(0, state.ActiveCount);
    }

    // -------------------------------------------------------------------------
    // execution_logs — AppendLog sequence + GetLogsAsync tail
    // -------------------------------------------------------------------------

    [Fact]
    public async Task AppendLog_assigns_monotonic_sequence_per_execution()
    {
        var id = await _fx.Store.StartAsync(Req("workbuddy"), "test", CancellationToken.None);

        await _fx.Store.AppendLogAsync(id, "stdout", "workbuddy", "line1", CancellationToken.None);
        await _fx.Store.AppendLogAsync(id, "stdout", "workbuddy", "line2", CancellationToken.None);
        await _fx.Store.AppendLogAsync(id, "stderr", "workbuddy", "err1", CancellationToken.None);
        await _fx.Store.AppendLogAsync(id, "stdout", "workbuddy", "line3", CancellationToken.None);

        // Query with a generous tail and verify content order is line1/line2/err1/line3.
        var logs = await _fx.Store.GetLogsAsync(id, tailBytes: 10 * 1024);
        var joined = string.Join("", logs.Select(t => t.Item3));
        Assert.Contains("line1", joined);
        var p1 = joined.IndexOf("line1", StringComparison.Ordinal);
        var p2 = joined.IndexOf("line2", StringComparison.Ordinal);
        var p3 = joined.IndexOf("err1",  StringComparison.Ordinal);
        var p4 = joined.IndexOf("line3", StringComparison.Ordinal);
        Assert.True(p1 < p2 && p2 < p3 && p3 < p4, $"order wrong: {p1} {p2} {p3} {p4}");
    }

    [Fact]
    public async Task GetLogsAsync_truncates_to_tail_bytes()
    {
        var id = await _fx.Store.StartAsync(Req("workbuddy"), "test", CancellationToken.None);
        // Append 50KB.
        var content = new string('a', 50_000);
        await _fx.Store.AppendLogAsync(id, "stdout", "workbuddy", content, CancellationToken.None);

        var tail10k = await _fx.Store.GetLogsAsync(id, tailBytes: 10_240);
        var joined10k = string.Join("", tail10k.Select(t => t.Item3));
        Assert.Equal(10_240, joined10k.Length);

        var tail100k = await _fx.Store.GetLogsAsync(id, tailBytes: 102_400);
        var joined100k = string.Join("", tail100k.Select(t => t.Item3));
        Assert.Equal(50_000, joined100k.Length);  // tail larger than content → return all
    }

    [Fact]
    public async Task GetLogsAsync_filters_by_stream()
    {
        var id = await _fx.Store.StartAsync(Req("workbuddy"), "test", CancellationToken.None);
        await _fx.Store.AppendLogAsync(id, "stdout", "workbuddy", "OUT_DATA", CancellationToken.None);
        await _fx.Store.AppendLogAsync(id, "stderr", "workbuddy", "ERR_DATA", CancellationToken.None);

        var stdoutOnly = await _fx.Store.GetLogsAsync(id, tailBytes: 10_240, stream: "stdout");
        var joined = string.Join("", stdoutOnly.Select(t => t.Item3));
        Assert.Contains("OUT_DATA", joined);
        Assert.DoesNotContain("ERR_DATA", joined);
    }

    // -------------------------------------------------------------------------
    // Heartbeat attempt/success split
    // -------------------------------------------------------------------------

    [Fact]
    public void Heartbeat_attempt_and_success_are_tracked_separately()
    {
        var state = NewState();

        // Simulate an attempt that didn't succeed (e.g. server returned 500).
        var attemptTime = DateTimeOffset.UtcNow;
        state.LastHeartbeatAttemptAt = attemptTime;
        // LastHeartbeatSuccessAt stays at MinValue.

        var doc = SnapshotJson(state, Array.Empty<string>());
        Assert.Equal(attemptTime, doc.GetProperty("last_heartbeat_attempt_at").GetDateTimeOffset());
        // success should still be MinValue (serialized as epoch or "0001-01-01...").
        var successStr = doc.GetProperty("last_heartbeat_success_at").ToString();
        Assert.True(successStr.Contains("0001-01-01") || successStr.Contains("MinValue"),
            $"expected MinValue marker, got {successStr}");
    }

    [Fact]
    public void Heartbeat_success_updates_only_success_field()
    {
        var state = NewState();

        var attemptTime = DateTimeOffset.UtcNow;
        var successTime = attemptTime.AddSeconds(5);
        state.LastHeartbeatAttemptAt = attemptTime;
        state.LastHeartbeatSuccessAt = successTime;

        var doc = SnapshotJson(state, Array.Empty<string>());
        Assert.Equal(attemptTime, doc.GetProperty("last_heartbeat_attempt_at").GetDateTimeOffset());
        Assert.Equal(successTime, doc.GetProperty("last_heartbeat_success_at").GetDateTimeOffset());
    }

    // -------------------------------------------------------------------------
    // Snapshot is correctly structured
    // -------------------------------------------------------------------------

    [Fact]
    public void Snapshot_includes_worker_state_basics()
    {
        var state = NewState();
        var doc = SnapshotJson(state, new[] { "workbuddy" });

        Assert.Equal("1.0.0", doc.GetProperty("version").GetString());
        Assert.Equal("online", doc.GetProperty("status").GetString());
        Assert.False(doc.GetProperty("paused").GetBoolean());
        // capacity fields
        var capacity = doc.GetProperty("capacity");
        Assert.Equal(1, capacity.GetProperty("max_concurrency").GetInt32());
        Assert.Equal(0, capacity.GetProperty("running").GetInt32());
    }

    [Fact]
    public void Snapshot_reports_busy_when_execution_in_flight()
    {
        var state = NewState();
        BeginAndCount(state, "workbuddy");

        var doc = SnapshotJson(state, new[] { "workbuddy" }, maxConcurrency: 1, running: 1, queued: 0);
        Assert.Equal("busy", doc.GetProperty("status").GetString());
    }

    [Fact]
    public void Snapshot_reports_paused_when_paused()
    {
        var state = NewState();
        state.Paused = true;
        var doc = SnapshotJson(state, Array.Empty<string>());
        Assert.Equal("paused", doc.GetProperty("status").GetString());
        Assert.True(doc.GetProperty("paused").GetBoolean());
    }

    // -------------------------------------------------------------------------
    // PerAgent 串扰防护 — one agent's increments don't leak to others
    // -------------------------------------------------------------------------

    [Fact]
    public void Incrementing_one_agent_does_not_affect_others()
    {
        var state = NewState();
        BeginAndCount(state, "workbuddy");
        BeginAndCount(state, "workbuddy");

        var doc = SnapshotJson(state, new[] { "workbuddy", "minimax", "codex" });
        Assert.Equal(2, doc.GetProperty("agents").GetProperty("workbuddy").GetProperty("running").GetInt32());
        Assert.Equal(2, doc.GetProperty("agents").GetProperty("workbuddy").GetProperty("total_executions").GetInt32());

        Assert.Equal(0, doc.GetProperty("agents").GetProperty("minimax").GetProperty("running").GetInt32());
        Assert.Equal(0, doc.GetProperty("agents").GetProperty("minimax").GetProperty("total_executions").GetInt32());

        Assert.Equal(0, doc.GetProperty("agents").GetProperty("codex").GetProperty("running").GetInt32());
        Assert.Equal(0, doc.GetProperty("agents").GetProperty("codex").GetProperty("total_executions").GetInt32());
    }

    // -------------------------------------------------------------------------
    // Helpers
    // -------------------------------------------------------------------------

    private WorkerState NewState() =>
        new(Options.Create(new WorkerOptions { Id = "test-worker", Version = "1.0.0" }));

    private ActiveExecution BeginAndCount(WorkerState state, string agent, long workloadId = 0)
    {
        var active = new ActiveExecution(
            ExecutionId: _nextExecId++,
            ExecutionKey: $"k{_nextExecId}",
            WorkloadType: "proposal",
            WorkloadId: workloadId,
            AgentType: agent,
            StartedAt: DateTimeOffset.UtcNow);
        state.Begin(active);
        state.IncrementAgentTotal(agent);
        return active;
    }

    private JsonElement SnapshotJson(WorkerState state, string[] registered, int maxConcurrency = 1, int running = 0, int queued = 0)
    {
        var snap = state.Snapshot(registered, maxConcurrency, running, queued);
        return JsonDocument.Parse(JsonSerializer.Serialize(snap)).RootElement.Clone();
    }
}
