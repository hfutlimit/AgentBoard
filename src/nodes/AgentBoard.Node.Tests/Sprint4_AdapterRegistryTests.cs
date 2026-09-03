using AgentBoard.Node;
using AgentBoard.Node.Agents;
using AgentBoard.Node.Execution;
using AgentBoard.Node.Tests.Fixtures;
using Microsoft.Extensions.Logging.Abstractions;
using Xunit;

namespace AgentBoard.Node.Tests;

public sealed class Sprint4_AdapterRegistryTests
{
    // -------------------------------------------------------------------------
    // Registry — multi-agent routing
    // -------------------------------------------------------------------------

    [Fact]
    public void Registry_routes_by_agent_type()
    {
        var workbuddy = FakeAgentAdapter.Success("workbuddy");
        var minimax = FakeAgentAdapter.Success("minimax");
        var codex = FakeAgentAdapter.Success("codex");
        var registry = new AgentAdapterRegistry(new IAgentAdapter[] { workbuddy, minimax, codex }, NullLogger<AgentAdapterRegistry>.Instance);

        Assert.Equal(workbuddy, registry.Get("workbuddy"));
        Assert.Equal(minimax, registry.Get("minimax"));
        Assert.Equal(codex, registry.Get("codex"));
    }

    [Fact]
    public void Registry_routing_is_case_insensitive()
    {
        var workbuddy = FakeAgentAdapter.Success("workbuddy");
        var registry = new AgentAdapterRegistry(new IAgentAdapter[] { workbuddy }, NullLogger<AgentAdapterRegistry>.Instance);
        Assert.Equal(workbuddy, registry.Get("WorkBuddy"));
        Assert.Equal(workbuddy, registry.Get("WORKBUDDY"));
    }

    [Fact]
    public void Registry_throws_for_unknown_agent()
    {
        var registry = new AgentAdapterRegistry(new[] { FakeAgentAdapter.Success("workbuddy") }, NullLogger<AgentAdapterRegistry>.Instance);
        var ex = Assert.Throws<InvalidAgentException>(() => registry.Get("unknown"));
        Assert.Equal("unknown", ex.AgentType);
    }

    [Fact]
    public void Registry_listed_agents_returns_all_registered()
    {
        var registry = new AgentAdapterRegistry(new IAgentAdapter[]
        {
            FakeAgentAdapter.Success("workbuddy"),
            FakeAgentAdapter.Success("minimax"),
            FakeAgentAdapter.Success("codex"),
        }, NullLogger<AgentAdapterRegistry>.Instance);

        Assert.Equal(3, registry.RegisteredAgents.Count);
        Assert.Contains("workbuddy", registry.RegisteredAgents);
        Assert.Contains("minimax", registry.RegisteredAgents);
        Assert.Contains("codex", registry.RegisteredAgents);
    }

    [Fact]
    public void Registry_isRegistered_checks_membership()
    {
        var registry = new AgentAdapterRegistry(new[] { FakeAgentAdapter.Success("workbuddy") }, NullLogger<AgentAdapterRegistry>.Instance);
        Assert.True(registry.IsRegistered("workbuddy"));
        Assert.False(registry.IsRegistered("minimax"));
        Assert.False(registry.IsRegistered(""));
    }

    // -------------------------------------------------------------------------
    // ProposalMessageMapper — backward compat + agent routing
    // -------------------------------------------------------------------------

    private static IAgentAdapterRegistry ThreeAgentRegistry() =>
        new AgentAdapterRegistry(new IAgentAdapter[]
        {
            FakeAgentAdapter.Success("workbuddy"),
            FakeAgentAdapter.Success("minimax"),
            FakeAgentAdapter.Success("codex"),
        }, NullLogger<AgentAdapterRegistry>.Instance);

    [Fact]
    public void Mapper_defaults_to_workbuddy_when_agent_type_missing()
    {
        var mapper = new ProposalMessageMapper(ThreeAgentRegistry());
        var msg = new ProposalMessage(ProposalId: 1, Round: 0, Reason: "r", Timestamp: "t", AgentType: null);
        var req = mapper.MapToExecution(msg, "test");

        Assert.Equal("workbuddy", req.AgentType);
        Assert.Equal("proposal:1:0:workbuddy", req.ExecutionKey);
    }

    [Fact]
    public void Mapper_uses_injected_default_agent_over_workbuddy_fallback()
    {
        // 2026-09-02 Glm53F bug regression: the injected Agents:DefaultAgent
        // must win over the "workbuddy" fallback (and must NOT be a hard-coded
        // slot name the C# registry never modeled).
        var mapper = new ProposalMessageMapper(ThreeAgentRegistry(), defaultAgent: "minimax");
        var msg = new ProposalMessage(ProposalId: 9, Round: 0, Reason: "r", Timestamp: "t", AgentType: null);
        var req = mapper.MapToExecution(msg, "test");

        Assert.Equal("minimax", req.AgentType);
        Assert.Equal("proposal:9:0:minimax", req.ExecutionKey);
    }

    [Fact]
    public void Mapper_server_agent_type_beats_injected_default()
    {
        var mapper = new ProposalMessageMapper(ThreeAgentRegistry(), defaultAgent: "minimax");
        var msg = new ProposalMessage(ProposalId: 10, Round: 0, Reason: "r", Timestamp: "t", AgentType: "codex");
        var req = mapper.MapToExecution(msg, "test");

        Assert.Equal("codex", req.AgentType);
    }

    [Fact]
    public void Mapper_uses_explicit_agent_type_when_present()
    {
        var mapper = new ProposalMessageMapper(ThreeAgentRegistry());
        var msg = new ProposalMessage(ProposalId: 2, Round: 1, Reason: "r", Timestamp: "t", AgentType: "minimax");
        var req = mapper.MapToExecution(msg, "test");

        Assert.Equal("minimax", req.AgentType);
        Assert.Equal("proposal:2:1:minimax", req.ExecutionKey);
    }

    [Fact]
    public void Mapper_throws_for_unknown_agent()
    {
        var mapper = new ProposalMessageMapper(ThreeAgentRegistry());
        var msg = new ProposalMessage(ProposalId: 3, Round: 0, Reason: "r", Timestamp: "t", AgentType: "gpt5");

        Assert.Throws<InvalidAgentException>(() => mapper.MapToExecution(msg, "test"));
    }

    [Fact]
    public void Mapper_includes_round_in_execution_key_for_safety()
    {
        // The same proposal_id with different rounds must produce different keys,
        // so RabbitMQ redeliveries at different rounds don't collide.
        var mapper = new ProposalMessageMapper(ThreeAgentRegistry());
        var r0 = mapper.MapToExecution(new ProposalMessage(1, 0, "r", "t", "workbuddy"), "test");
        var r1 = mapper.MapToExecution(new ProposalMessage(1, 1, "r", "t", "workbuddy"), "test");
        Assert.NotEqual(r0.ExecutionKey, r1.ExecutionKey);
    }

    [Fact]
    public void Mapper_distinguishes_agents_per_proposal()
    {
        // Two different agents working on the same proposal must produce different
        // execution keys, so e.g. workbuddy and minimax running on the same
        // proposal don't suppress each other via idempotency.
        var mapper = new ProposalMessageMapper(ThreeAgentRegistry());
        var workbuddy = mapper.MapToExecution(new ProposalMessage(1, 0, "r", "t", "workbuddy"), "test");
        var minimax = mapper.MapToExecution(new ProposalMessage(1, 0, "r", "t", "minimax"), "test");
        var codex = mapper.MapToExecution(new ProposalMessage(1, 0, "r", "t", "codex"), "test");
        Assert.Equal(3, new[] { workbuddy.ExecutionKey, minimax.ExecutionKey, codex.ExecutionKey }.Distinct().Count());
    }

    // -------------------------------------------------------------------------
    // FakeAgentAdapter call-counting proves routing
    // -------------------------------------------------------------------------

    [Fact]
    public async Task Coordinator_dispatches_to_adapter_based_on_request_agent_type()
    {
        // Use a real (in-memory) DB to drive the full path.
        var opts = new Microsoft.Extensions.Options.OptionsWrapper<NodeOptions>(new NodeOptions
        {
            Id = "test",
            HistoryDatabasePath = Path.Combine(Path.GetTempPath(), $"reg-test-{Guid.NewGuid():N}.db"),
        });
        var store = new ExecutionStore(opts, NullLogger<ExecutionStore>.Instance);
        var inbox = new InboxStore(store, NullLogger<InboxStore>.Instance);
        var workbuddy = FakeAgentAdapter.Success("workbuddy");
        var minimax = FakeAgentAdapter.Success("minimax");
        var codex = FakeAgentAdapter.Success("codex");
        var registry = new AgentAdapterRegistry(new IAgentAdapter[] { workbuddy, minimax, codex }, NullLogger<AgentAdapterRegistry>.Instance);
        var state = new WorkerState(opts, new WorkerIdentity(opts));
        var channel = new ExecutionChannel(opts);
        var coord = new ExecutionCoordinator(store, inbox, channel, registry, state, NullLogger<ExecutionCoordinator>.Instance);

        foreach (var (agent, id) in new[] { ("workbuddy", 1L), ("minimax", 2L), ("codex", 3L) })
        {
            var req = new ExecutionRequest($"proposal:{id}:0:{agent}", "proposal", id, agent, 0, "test", "{}");
            var (inboxId, _) = await inbox.TryEnqueueAsync(req, CancellationToken.None);
            await coord.ExecuteAsync(req, inboxId, CancellationToken.None);
        }

        Assert.Equal(1, workbuddy.CallCount);
        Assert.Equal(1, minimax.CallCount);
        Assert.Equal(1, codex.CallCount);

        try { File.Delete(opts.Value.HistoryDatabasePath); } catch { }
    }
}
