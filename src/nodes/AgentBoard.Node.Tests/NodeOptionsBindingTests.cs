using AgentBoard.Node;
using Microsoft.Extensions.Configuration;
using Xunit;

namespace AgentBoard.Node.Tests;

/// <summary>
/// P7b: the "Worker"/"Node" section merge is the one piece of the rename that
/// has to keep working for BOTH a freshly-migrated install (only "Node") and an
/// already-deployed one (only "Worker", with operator-chosen numbers). These
/// tests pin that behaviour per key, not just for the string "Id".
/// </summary>
public sealed class NodeOptionsBindingTests
{
    private static NodeOptions Bind(params (string Key, string Value)[] settings)
    {
        var configuration = new ConfigurationBuilder()
            .AddInMemoryCollection(settings.ToDictionary(s => s.Key, s => (string?)s.Value))
            .Build();

        // Mirrors Program.cs: legacy section first as the baseline, then the
        // canonical section layered on top.
        var options = new NodeOptions();
        configuration.GetSection("Worker").Bind(options);
        NodeOptions.BindNonEmpty(configuration.GetSection("Node"), options);
        return options;
    }

    [Fact]
    public void LegacyWorkerValuesSurviveWhenNodeSectionIsAbsent()
    {
        var options = Bind(
            ("Worker:Id", "legacy-01"),
            ("Worker:HeartbeatSeconds", "5"),
            ("Worker:HistoryDatabasePath", @"D:\custom\node.db"),
            ("Worker:MaxConcurrentExecutions", "4"),
            ("Worker:MaxPendingInbox", "250"));

        Assert.Equal("legacy-01", options.Id);
        Assert.Equal(5, options.HeartbeatSeconds);
        Assert.Equal(@"D:\custom\node.db", options.HistoryDatabasePath);
        Assert.Equal(4, options.MaxConcurrentExecutions);
        Assert.Equal(250, options.MaxPendingInbox);
    }

    [Fact]
    public void EmptyNodeIdDoesNotBlankOutLegacyWorkerId()
    {
        var options = Bind(
            ("Worker:Id", "legacy-01"),
            ("Worker:HeartbeatSeconds", "5"),
            ("Node:Id", ""));

        Assert.Equal("legacy-01", options.Id);
        Assert.Equal(5, options.HeartbeatSeconds);
    }

    [Fact]
    public void NodeSectionOverridesOnlyTheKeysItDeclares()
    {
        // This is the regression that a naive "copy every non-empty property"
        // merge causes: the scratch object carries constructor defaults, so a
        // Node section that merely omits HeartbeatSeconds would push 15 over
        // the operator's 5. Same for the db path and the concurrency limit.
        var options = Bind(
            ("Worker:Id", "legacy-01"),
            ("Worker:HeartbeatSeconds", "5"),
            ("Worker:HistoryDatabasePath", @"D:\custom\node.db"),
            ("Worker:MaxConcurrentExecutions", "4"),
            ("Node:HeartbeatSeconds", "30"));

        Assert.Equal(30, options.HeartbeatSeconds);
        // Keys the Node section does not mention keep their legacy values.
        Assert.Equal("legacy-01", options.Id);
        Assert.Equal(@"D:\custom\node.db", options.HistoryDatabasePath);
        Assert.Equal(4, options.MaxConcurrentExecutions);
    }

    [Fact]
    public void NodeOnlyConfigurationIsUsedWhenLegacySectionIsAbsent()
    {
        var options = Bind(
            ("Node:Id", "node-01"),
            ("Node:HeartbeatSeconds", "60"),
            ("Node:HistoryDatabasePath", @"E:\node\history.db"),
            ("Node:MaxConcurrentExecutions", "8"));

        Assert.Equal("node-01", options.Id);
        Assert.Equal(60, options.HeartbeatSeconds);
        Assert.Equal(@"E:\node\history.db", options.HistoryDatabasePath);
        Assert.Equal(8, options.MaxConcurrentExecutions);
    }

    [Fact]
    public void ShippedDefaultsApplyWhenNeitherSectionIsPresent()
    {
        var options = Bind();

        Assert.Equal(Environment.MachineName, options.Id);
        Assert.Equal(15, options.HeartbeatSeconds);
        Assert.Equal(@"data\proposal-worker.db", options.HistoryDatabasePath);
        Assert.Equal(1, options.MaxConcurrentExecutions);
        Assert.Equal(1000, options.MaxPendingInbox);
    }

    [Fact]
    public void NodeSectionWithOnlyEmptyValuesLeavesLegacyIntact()
    {
        // The failure mode this whole merge exists to prevent: appsettings.json
        // ships a Node section full of placeholder defaults. If those
        // placeholders win, the worker registers with an empty id and /health
        // reports a blank worker_id.
        var options = Bind(
            ("Worker:Id", "legacy-01"),
            ("Worker:HeartbeatSeconds", "5"),
            ("Node:Id", ""),
            ("Node:HeartbeatSeconds", "0"));

        Assert.Equal("legacy-01", options.Id);
        // 0 is a real value (not a placeholder) - Node declares the key, so it
        // wins even though it looks "empty-ish" to a naive string check.
        Assert.Equal(0, options.HeartbeatSeconds);
    }
}
