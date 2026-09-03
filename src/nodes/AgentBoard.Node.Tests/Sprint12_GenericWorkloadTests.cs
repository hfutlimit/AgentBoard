// SPDX-License-Identifier: MIT
using System.Text;
using System.Text.Json;
using AgentBoard.Node;
using AgentBoard.Node.Agents;
using AgentBoard.Node.Execution;
using AgentBoard.Node.Tests.Fixtures;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Xunit;

namespace AgentBoard.Node.Tests;

/// <summary>
/// Sprint 12 — Generic AgentWorker. Closes the orchestration gap the
/// 2026-08-30 review flagged: the worker now accepts both proposal
/// payloads (legacy <c>proposal_id</c> shape) and workflow events
/// (<c>event</c> shape) on the same consumer; <see cref="WorkflowMessage"/>
/// additionally drives a second consumer that subscribes to
/// <c>agentboard.workflow</c> directly.
///
/// These tests cover the parsing + mapping + inbox layers in isolation
/// and one full dispatcher-driven E2E. The cross-component E2E
/// (proposal → converge → ticket → task.available → developer →
/// review → reject → rework → approve) is documented but not
/// implemented here because it requires the FastAPI backend in the
/// loop; the .NET worker cannot independently exercise state-machine
/// transitions the backend owns (claim_development_task,
/// submit_task_for_review, review_vote, etc.).
/// </summary>
public sealed class Sprint12_GenericWorkloadTests
{
    // -------------------------------------------------------------------------
    // WorkloadMessage.Parse — discriminator
    // -------------------------------------------------------------------------

    [Fact]
    public void WorkloadMessage_Parse_classifies_proposal_payload()
    {
        var payload = """{"proposal_id":42,"round":0,"reason":"r","ts":"t"}""";
        var msg = WorkloadMessage.Parse(Encoding.UTF8.GetBytes(payload));
        var proposal = Assert.IsType<WorkloadMessage.Proposal>(msg);
        Assert.Equal(42, proposal.Inner.ProposalId);
    }

    [Fact]
    public void WorkloadMessage_Parse_classifies_workflow_payload()
    {
        var payload = """{"event":"task.available","entity_type":"task","entity_id":7,"ref_id":null,"ts":"t"}""";
        var msg = WorkloadMessage.Parse(Encoding.UTF8.GetBytes(payload));
        var wf = Assert.IsType<WorkloadMessage.Workflow>(msg);
        Assert.Equal("task.available", wf.Inner.Event);
        Assert.Equal(7, wf.Inner.EntityId);
    }

    [Fact]
    public void WorkloadMessage_Parse_rejects_payload_with_neither_proposal_id_nor_event()
    {
        var payload = """{"foo":1}""";
        Assert.Throws<InvalidDataException>(() => WorkloadMessage.Parse(Encoding.UTF8.GetBytes(payload)));
    }

    [Fact]
    public void WorkloadMessage_Parse_rejects_non_object_payload()
    {
        var payload = """[1,2,3]""";
        Assert.Throws<InvalidDataException>(() => WorkloadMessage.Parse(Encoding.UTF8.GetBytes(payload)));
    }

    // -------------------------------------------------------------------------
    // WorkflowMessage.Parse — schema validation
    // -------------------------------------------------------------------------

    [Theory]
    [InlineData("task.available")]
    [InlineData("task.assigned")]
    [InlineData("task.ready_for_review")]
    [InlineData("task.review_requested")]
    [InlineData("task.rejected")]
    [InlineData("task.review_rejected")]
    [InlineData("proposal.ticket_requested")]
    [InlineData("proposal.ticket_created")]
    public void WorkflowMessage_Parse_accepts_known_event(string eventName)
    {
        var payload = $$"""{"event":"{{eventName}}","entity_type":"task","entity_id":1,"ts":"t"}""";
        var msg = WorkflowMessage.Parse(Encoding.UTF8.GetBytes(payload));
        Assert.Equal(eventName, msg.Event);
        Assert.Null(msg.RefId);
    }

    [Fact]
    public void WorkflowMessage_Parse_preserves_ref_id_when_positive()
    {
        var payload = """{"event":"task.rejected","entity_type":"task","entity_id":9,"ref_id":3,"ts":"t"}""";
        var msg = WorkflowMessage.Parse(Encoding.UTF8.GetBytes(payload));
        Assert.Equal(3, msg.RefId);
    }

    [Fact]
    public void WorkflowMessage_Parse_accepts_unknown_event_letting_mapper_decide()
    {
        // Design note: the parser is intentionally lenient. The mapper
        // owns the routing table (Classify) and is the right place to
        // reject events the .NET worker cannot act on — so the broker
        // can still observe / audit unmapped events in the FastAPI
        // workflow worker, and a future adapter can adopt them by
        // adding one row to Classify without touching the parser.
        var payload = """{"event":"made.up.event","entity_type":"task","entity_id":1}""";
        var msg = WorkflowMessage.Parse(Encoding.UTF8.GetBytes(payload));
        Assert.Equal("made.up.event", msg.Event);
    }

    [Fact]
    public void WorkflowMessage_Parse_rejects_non_positive_entity_id()
    {
        var payload = """{"event":"task.available","entity_type":"task","entity_id":0}""";
        Assert.Throws<InvalidDataException>(() => WorkflowMessage.Parse(Encoding.UTF8.GetBytes(payload)));
    }

    // -------------------------------------------------------------------------
    // WorkflowMessageMapper — event → WorkloadType routing
    // -------------------------------------------------------------------------

    public static IEnumerable<object[]> ActionableEventRoutings() => new[]
    {
        new object[] { "task.available",          WorkloadTypes.Task },
        new object[] { "task.assigned",           WorkloadTypes.Task },
        // PR-4: task.ready_for_review 是 pre-assignment 事件，由 Python
        // workflow_worker 独占 internal_queue 选 reviewer；.NET 不再
        // 路由它（落到 DLQ 暴露 routing 错配）。从 actionable 表移除。
        new object[] { "task.review_requested",   WorkloadTypes.Review },
        new object[] { "task.rejected",           WorkloadTypes.Rework },
        new object[] { "task.review_rejected",    WorkloadTypes.Rework },
        new object[] { "proposal.ticket_requested", WorkloadTypes.Ticket },
        new object[] { "proposal.ticket_created",   WorkloadTypes.Ticket },
    };

    [Theory]
    [MemberData(nameof(ActionableEventRoutings))]
    public void WorkflowMessageMapper_routes_actionable_events_to_expected_workload(
        string eventName, string expectedWorkload)
    {
        var mapper = new WorkflowMessageMapper(ThreeAgentRegistry());
        var msg = MakeWorkflow(eventName, entityId: 1, refId: null);
        var req = mapper.MapToExecution(msg, source: "broadcast");
        Assert.Equal(expectedWorkload, req.WorkloadType);
        Assert.Equal(1, req.WorkloadId);
    }

    [Fact]
    public void WorkflowMessageMapper_throws_for_non_actionable_event()
    {
        var mapper = new WorkflowMessageMapper(ThreeAgentRegistry());
        // Story/comment/review.vote_cast — surfaced by the broker but not
        // actionable. Mapper must reject so the consumer DLQs.
        var msg = MakeWorkflow("story.created", entityId: 1);
        var ex = Assert.Throws<InvalidDataException>(
            () => mapper.MapToExecution(msg, "broadcast"));
        Assert.Contains("story.created", ex.Message);
    }

    [Fact]
    public void WorkflowMessageMapper_rejects_pre_assignment_event()
    {
        // PR-4: task.ready_for_review 是 pre-assignment 事件（任务进入
        // in_review 还没 reviewer），由 Python workflow_worker 独占
        // internal_queue 处理。.NET 收到应拒（→ DLQ）暴露 routing 错配。
        var mapper = new WorkflowMessageMapper(ThreeAgentRegistry());
        var msg = MakeWorkflow("task.ready_for_review", entityId: 1, agent: "workbuddy");
        var ex = Assert.Throws<InvalidDataException>(
            () => mapper.MapToExecution(msg, "broadcast"));
        Assert.Contains("task.ready_for_review", ex.Message);
    }

    [Fact]
    public void WorkflowMessageMapper_rejects_unregistered_agent()
    {
        var mapper = new WorkflowMessageMapper(ThreeAgentRegistry());
        var msg = MakeWorkflow("task.available", entityId: 1, agent: "gpt5");
        Assert.Throws<InvalidAgentException>(() => mapper.MapToExecution(msg, "broadcast"));
    }

    [Fact]
    public void WorkflowMessageMapper_throws_when_agent_type_missing_and_no_default()
    {
        // PR-3: 缺 agent_type 且没显式 default → 抛 InvalidDataException
        // 进 DLQ（之前版本静默 default "workbuddy" 把所有 task / review
        // 路由到 WorkBuddy，是 P0-4 根因）。
        var mapper = new WorkflowMessageMapper(ThreeAgentRegistry());
        var msg = MakeWorkflow("task.available", entityId: 1, agent: null);
        var ex = Assert.Throws<InvalidDataException>(
            () => mapper.MapToExecution(msg, "broadcast"));
        Assert.Contains("missing 'agent_type'", ex.Message);
        Assert.Contains("task.available", ex.Message);
    }

    [Fact]
    public void WorkflowMessageMapper_uses_configured_default_when_agent_type_missing()
    {
        // PR-3: operator 显式配置 default（dev / integration 兜底）→ 用 default
        // 并保留原行为。生产应配 null 让缺 agent_type 抛 DLQ。
        var mapper = new WorkflowMessageMapper(ThreeAgentRegistry(), defaultAgent: "workbuddy");
        var msg = MakeWorkflow("task.available", entityId: 1, agent: null);
        var req = mapper.MapToExecution(msg, "broadcast");
        Assert.Equal("workbuddy", req.AgentType);
        Assert.Equal(WorkloadTypes.Task, req.WorkloadType);
    }

    [Fact]
    public void WorkflowMessageMapper_execution_key_distinguishes_ref_rounds_for_rework()
    {
        // Same task, same event, different review round → distinct keys.
        // Critical so rework round=2 doesn't dedupe-against the round=1 row.
        var mapper = new WorkflowMessageMapper(ThreeAgentRegistry());
        var r1 = mapper.MapToExecution(MakeWorkflow("task.rejected", 1, refId: 1), "agent");
        var r2 = mapper.MapToExecution(MakeWorkflow("task.rejected", 1, refId: 2), "agent");
        Assert.NotEqual(r1.ExecutionKey, r2.ExecutionKey);
    }

    [Fact]
    public void WorkflowMessageMapper_execution_key_distinguishes_agents()
    {
        var mapper = new WorkflowMessageMapper(ThreeAgentRegistry());
        var wb = mapper.MapToExecution(MakeWorkflow("task.available", 1, agent: "workbuddy"), "agent");
        var m2 = mapper.MapToExecution(MakeWorkflow("task.available", 1, agent: "minimax"), "agent");
        Assert.NotEqual(wb.ExecutionKey, m2.ExecutionKey);
    }

    [Fact]
    public void WorkflowMessageMapper_payload_roundtrips_through_json()
    {
        var mapper = new WorkflowMessageMapper(ThreeAgentRegistry());
        var msg = MakeWorkflow("task.review_requested", entityId: 7, refId: 2, agent: "codex");
        var req = mapper.MapToExecution(msg, "broadcast");
        // Re-parse the payload to make sure the mapper serialised everything
        // the dispatcher / adapter will need.
        using var doc = JsonDocument.Parse(req.PayloadJson);
        var root = doc.RootElement;
        Assert.Equal("task.review_requested", root.GetProperty("event").GetString());
        Assert.Equal(7, root.GetProperty("entity_id").GetInt64());
        Assert.Equal(2, root.GetProperty("ref_id").GetInt64());
        Assert.Equal("codex", root.GetProperty("agent_type").GetString());
    }

    // -------------------------------------------------------------------------
    // End-to-end: enqueue workflow event → inbox → dispatcher → fake adapter
    // -------------------------------------------------------------------------

    [Fact]
    public async Task Workflow_event_flows_through_inbox_to_dispatcher_and_completes()
    {
        // Arrange: real DI graph (no broker, no external CLI).
        using var fx = new TempDbFixture();
        var fake = FakeAgentAdapter.Success("workbuddy", outputJson: """{"action":"ok"}""");
        var registry = new AgentAdapterRegistry(
            new IAgentAdapter[] { fake, FakeAgentAdapter.Success("minimax"), FakeAgentAdapter.Success("codex") },
            NullLogger<AgentAdapterRegistry>.Instance);
        var channel = new ExecutionChannel(Options.Create(fx.Options));
        var state = new WorkerState(Options.Create(fx.Options), new WorkerIdentity(Options.Create(fx.Options)));

        // Map a workflow event the way the consumer would.
        var mapper = new WorkflowMessageMapper(registry);
        var msg = MakeWorkflow("task.available", entityId: 17, refId: 0);
        var req = mapper.MapToExecution(msg, source: "broadcast");
        Assert.Equal(WorkloadTypes.Task, req.WorkloadType);

        // Enqueue the way the consumer would.
        var (outcome, inboxId) = await fx.Inbox.TryEnqueueWithinCapacityAsync(
            req, fx.Options.MaxPendingInbox, CancellationToken.None);
        Assert.Equal(InboxStore.EnqueueWithinCapacityOutcome.Enqueued, outcome);
        Assert.True(inboxId > 0);

        // Start a real dispatcher + coordinator so the channel gets drained
        // and the inbox row reaches "completed" — same as Sprint8 E2E but
        // for a workflow event instead of a proposal.
        var coordinator = new ExecutionCoordinator(
            fx.Store, fx.Inbox, channel, registry, state, NullLogger<ExecutionCoordinator>.Instance);
        var dispatcher = new ExecutionDispatcher(
            channel, fx.Inbox, coordinator, NullLogger<ExecutionDispatcher>.Instance);
        using var dispatcherCts = new CancellationTokenSource(TimeSpan.FromSeconds(10));
        await dispatcher.StartAsync(dispatcherCts.Token);
        await channel.Writer.WriteAsync(
            new WakeSignal { At = DateTimeOffset.UtcNow, Source = "test-workflow" },
            CancellationToken.None);

        var reached = await WaitForInboxTerminal(fx, inboxId, TimeSpan.FromSeconds(5));
        Assert.True(reached, "Inbox row did not reach terminal status within 5s");

        await dispatcher.StopAsync(CancellationToken.None);

        // Adapter was invoked exactly once with the workflow payload.
        Assert.Equal(1, fake.CallCount);
        Assert.NotNull(fake.LastContext);
        Assert.Equal(WorkloadTypes.Task, fake.LastContext!.WorkloadType);
        Assert.Equal(17, fake.LastContext.WorkloadId);
    }

    [Fact]
    public async Task Workflow_event_redelivery_is_idempotent()
    {
        // Same execution_key → INSERT OR IGNORE → Duplicate, no second run.
        using var fx = new TempDbFixture();
        var fake = FakeAgentAdapter.Success("workbuddy");
        var registry = new AgentAdapterRegistry(
            new IAgentAdapter[] { fake, FakeAgentAdapter.Success("minimax") },
            NullLogger<AgentAdapterRegistry>.Instance);
        var channel = new ExecutionChannel(Options.Create(fx.Options));
        var state = new WorkerState(Options.Create(fx.Options), new WorkerIdentity(Options.Create(fx.Options)));

        var mapper = new WorkflowMessageMapper(registry);
        var req = mapper.MapToExecution(
            MakeWorkflow("task.available", entityId: 99, refId: 0), "broadcast");

        var (first, _) = await fx.Inbox.TryEnqueueWithinCapacityAsync(
            req, fx.Options.MaxPendingInbox, CancellationToken.None);
        var (second, _) = await fx.Inbox.TryEnqueueWithinCapacityAsync(
            req, fx.Options.MaxPendingInbox, CancellationToken.None);
        Assert.Equal(InboxStore.EnqueueWithinCapacityOutcome.Enqueued, first);
        Assert.Equal(InboxStore.EnqueueWithinCapacityOutcome.Duplicate, second);
    }

    // -------------------------------------------------------------------------
    // End-to-end (deeper): reject → rework cycle, but within the worker's
    // own scope. The .NET worker cannot drive the backend state machine
    // (claim_development_task, submit_task_for_review, review_vote, etc.)
    // by itself — those endpoints live in FastAPI. What we CAN exercise
    // is the inbox round-trip: a rejected event arrives, the mapper turns
    // it into a Rework ExecutionRequest, the dispatcher runs the dev
    // adapter, and the second submit produces an Approve path's
    // ExecutionRequest.
    // -------------------------------------------------------------------------

    [Fact]
    public async Task Reject_then_rework_two_workflow_events_both_complete()
    {
        using var fx = new TempDbFixture();
        var dev = FakeAgentAdapter.Success("workbuddy");
        var registry = new AgentAdapterRegistry(
            new IAgentAdapter[] { dev, FakeAgentAdapter.Success("minimax") },
            NullLogger<AgentAdapterRegistry>.Instance);
        var channel = new ExecutionChannel(Options.Create(fx.Options));
        var state = new WorkerState(Options.Create(fx.Options), new WorkerIdentity(Options.Create(fx.Options)));
        var mapper = new WorkflowMessageMapper(registry);

        // Round 1: developer picks up the task.
        var develop = mapper.MapToExecution(
            MakeWorkflow("task.available", entityId: 5, refId: 0), "broadcast");
        // Round 1 review rejects — ref=1 (first review round).
        var rework = mapper.MapToExecution(
            MakeWorkflow("task.rejected", entityId: 5, refId: 1), "agent");

        Assert.Equal(WorkloadTypes.Task, develop.WorkloadType);
        Assert.Equal(WorkloadTypes.Rework, rework.WorkloadType);
        Assert.NotEqual(develop.ExecutionKey, rework.ExecutionKey);

        var (devOutcome, devInbox) = await fx.Inbox.TryEnqueueWithinCapacityAsync(
            develop, fx.Options.MaxPendingInbox, CancellationToken.None);
        var (reworkOutcome, reworkInbox) = await fx.Inbox.TryEnqueueWithinCapacityAsync(
            rework, fx.Options.MaxPendingInbox, CancellationToken.None);
        Assert.Equal(InboxStore.EnqueueWithinCapacityOutcome.Enqueued, devOutcome);
        Assert.Equal(InboxStore.EnqueueWithinCapacityOutcome.Enqueued, reworkOutcome);
        Assert.NotEqual(devInbox, reworkInbox);

        var coordinator = new ExecutionCoordinator(
            fx.Store, fx.Inbox, channel, registry, state, NullLogger<ExecutionCoordinator>.Instance);
        var dispatcher = new ExecutionDispatcher(
            channel, fx.Inbox, coordinator, NullLogger<ExecutionDispatcher>.Instance);
        using var dispatcherCts = new CancellationTokenSource(TimeSpan.FromSeconds(10));
        await dispatcher.StartAsync(dispatcherCts.Token);
        await channel.Writer.WriteAsync(
            new WakeSignal { At = DateTimeOffset.UtcNow, Source = "test" },
            CancellationToken.None);

        var devDone = await WaitForInboxTerminal(fx, devInbox, TimeSpan.FromSeconds(5));
        var reworkDone = await WaitForInboxTerminal(fx, reworkInbox, TimeSpan.FromSeconds(5));
        await dispatcher.StopAsync(CancellationToken.None);

        Assert.True(devDone, "Initial develop run did not complete");
        Assert.True(reworkDone, "Rework run did not complete");
        // Two adapter invocations: one for develop, one for rework.
        Assert.Equal(2, dev.CallCount);
    }

    // -------------------------------------------------------------------------
    // Helpers
    // -------------------------------------------------------------------------

    private static IAgentAdapterRegistry ThreeAgentRegistry() => new AgentAdapterRegistry(
        new IAgentAdapter[]
        {
            FakeAgentAdapter.Success("workbuddy"),
            FakeAgentAdapter.Success("minimax"),
            FakeAgentAdapter.Success("codex"),
        },
        NullLogger<AgentAdapterRegistry>.Instance);

    private static WorkflowMessage MakeWorkflow(
        string eventName, long entityId, long? refId = null, string? agent = "workbuddy") =>
        new(eventName, "task", entityId, refId, "ts", agent);

    private static async Task<bool> WaitForInboxTerminal(
        TempDbFixture fx, long inboxId, TimeSpan timeout)
    {
        var deadline = DateTimeOffset.UtcNow + timeout;
        while (DateTimeOffset.UtcNow < deadline)
        {
            var row = await fx.Inbox.GetAsync(inboxId);
            if (row is not null && (row.Status == "completed" || row.Status == "failed"))
                return true;
            await Task.Delay(50);
        }
        return false;
    }
}
