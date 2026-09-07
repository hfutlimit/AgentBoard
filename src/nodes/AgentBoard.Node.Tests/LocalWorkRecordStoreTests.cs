using System.Text.Json.Nodes;
using AgentBoard.Node.WorkerOwned;
using Microsoft.Data.Sqlite;
using Xunit;

namespace AgentBoard.Node.Tests;

public sealed class LocalWorkRecordStoreTests : IDisposable
{
    private readonly string _path = Path.Combine(Path.GetTempPath(), $"local-work-records-{Guid.NewGuid():N}.db");
    public void Dispose()
    {
        SqliteConnection.ClearAllPools();
        foreach (var suffix in new[] { "", "-wal", "-shm" }) if (File.Exists(_path + suffix)) File.Delete(_path + suffix);
    }

    [Fact]
    public void Attempt_is_scoped_idempotent_and_has_ordered_events()
    {
        var store = new LocalWorkRecordStore(_path, "https://server.test|worker-a");
        var start = new LocalWorkRecordStart(9, "secret-token", "agent-a", "codex", "gpt-5.6-sol", "dev", "task #9");
        var id = store.CreateRunning(start);
        Assert.Equal(id, store.CreateRunning(start));
        store.MarkPending(id, WorkRecordProjection.From("dev", new JsonObject { ["commit"] = new string('a', 40), ["prompt"] = "do not store" }));
        store.MarkSucceeded(id);
        var detail = Assert.IsType<LocalWorkRecordDetail>(store.Get(id));
        Assert.Equal(LocalWorkRecordStates.Succeeded, detail.Summary.State);
        Assert.Equal(["created", "journal_result_saved", "completion_confirmed"], detail.Events.Select(e => e.Code));
        Assert.DoesNotContain("prompt", detail.ResultDetail!, StringComparison.OrdinalIgnoreCase);
        Assert.Throws<InvalidOperationException>(() => new LocalWorkRecordStore(_path, "https://other.test|worker-a"));
    }

    [Fact]
    public void Pagination_cursor_is_signed_and_recovery_is_honest()
    {
        var store = new LocalWorkRecordStore(_path, "https://server.test|worker-a");
        for (var i = 0; i < 3; i++) store.CreateRunning(new(i + 1, "token-" + i, "agent-a", "codex", "model", "dev", "task #" + i));
        store.RecoverInterrupted();
        var page = store.List("agent-a", 2, LocalWorkRecordStates.Interrupted, null);
        Assert.Equal(2, page.Items.Count);
        Assert.NotNull(page.NextCursor);
        Assert.Single(store.List("agent-a", 2, LocalWorkRecordStates.Interrupted, page.NextCursor).Items);
        var invalid = page.NextCursor![..^1] + "x";
        Assert.Throws<ArgumentException>(() => store.List("agent-a", 2, LocalWorkRecordStates.Interrupted, invalid));
        Assert.All(store.List("agent-a", 10, null, null).Items, item => Assert.Equal(LocalWorkRecordStates.Interrupted, item.State));
    }

    [Fact]
    public void Journal_result_replayed_after_restart_can_become_pending_but_terminal_records_do_not_regress()
    {
        var store = new LocalWorkRecordStore(_path, "https://server.test|worker-a");
        var id = store.CreateRunning(new(11, "token-11", "agent-a", "codex", "model", "dev", "task #11"));
        store.RecoverInterrupted();
        store.MarkPending(id, WorkRecordProjection.From("dev", new JsonObject { ["commit"] = new string('c', 40) }));
        store.MarkSucceeded(id);

        Assert.Throws<InvalidOperationException>(() => store.MarkFailed(id, "ProviderFailed"));
        var detail = Assert.IsType<LocalWorkRecordDetail>(store.Get(id));
        Assert.Equal(LocalWorkRecordStates.Succeeded, detail.Summary.State);
        Assert.Equal(["created", "recovered_interrupted", "journal_result_saved", "completion_confirmed"],
            detail.Events.Select(e => e.Code));
    }

    [Fact]
    public void Projection_and_redactor_do_not_return_paths_or_tokens()
    {
        var projection = WorkRecordProjection.From("dev", new JsonObject { ["commit"] = new string('b', 40), ["context"] = "C:\\secret\\context.json" });
        Assert.DoesNotContain("context", projection.Detail, StringComparison.OrdinalIgnoreCase);
        var cleaned = WorkRecordRedactor.Clean("Bearer abcdefghijklmnop C:\\temp\\secret token=abc", 500);
        Assert.DoesNotContain("abcdefghijklmnop", cleaned);
        Assert.DoesNotContain("C:\\temp", cleaned);
    }

    [Theory]
    [InlineData("proposal")]
    [InlineData("design")]
    [InlineData("design_review")]
    [InlineData("dev")]
    [InlineData("dev_review")]
    [InlineData("qa")]
    [InlineData("qa_review")]
    public void Every_work_kind_projection_ignores_untrusted_result_fields(string kind)
    {
        var output = new JsonObject
        {
            ["commit"] = new string('d', 40),
            ["decision"] = "approve",
            ["tests_passed"] = true,
            ["prompt"] = "secret prompt",
            ["context"] = "C:\\private\\context.json",
            ["token"] = "token=should-not-appear",
            ["summary"] = "untrusted result text",
            ["defects"] = new JsonArray(new JsonObject { ["title"] = "secret finding", ["description"] = "C:\\private\\evidence" })
        };

        var projection = WorkRecordProjection.From(kind, output);
        Assert.DoesNotContain("secret", projection.Summary + projection.Detail, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("context", projection.Summary + projection.Detail, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("untrusted", projection.Summary + projection.Detail, StringComparison.OrdinalIgnoreCase);
        if (kind == "qa") Assert.Contains("1 defects", projection.Summary);
    }
}
