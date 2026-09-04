// SPDX-License-Identifier: MIT
using AgentBoard.Contracts;
using AgentBoard.Node.Agents;
using AgentBoard.Node.Durable;
using AgentBoard.Node.Tests.Fixtures;
using Microsoft.Extensions.Logging.Abstractions;
using Xunit;

namespace AgentBoard.Node.Tests.Durable;

public sealed class DurableAssignmentRunnerTests
{
    private const string Worker = "worker-durable";
    private const string Trace = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01";
    private DateTimeOffset _now = new(2026, 9, 4, 0, 0, 0, TimeSpan.Zero);

    [Fact]
    public async Task Accepted_command_runs_through_pep_and_adapter_then_durably_records_result()
    {
        var policy = CompiledPolicy.Compile(PolicyPresets.Developer,
            new Dictionary<string, PolicyDecision>());
        var adapter = FakeAgentAdapter.Success("scenario", "{\"result\":\"ok\"}");
        var journal = new InMemoryNodeCommandJournal();
        var transport = new HoldingTransport();
        var outbox = new LocalResultOutbox(transport, () => _now);
        var runner = Runner(journal, outbox, adapter, policy);
        var command = Assign(policy.RevisionId, taskContext: "review task 42");

        Assert.Equal(AcceptanceKind.Accepted, runner.Accept(command).Kind);
        Assert.Single(journal.Pending());
        await runner.ExecuteAcceptedAsync(command, CancellationToken.None);

        Assert.Equal(1, adapter.CallCount);
        Assert.Equal("review task 42", adapter.LastContext!.Prompt);
        Assert.Empty(journal.Pending());
        var result = Assert.Single(outbox.Records).Result;
        Assert.Equal(AttemptResultStatus.Succeeded, result.ResultStatus);
        Assert.Empty(EnvelopeValidator.ValidateResultFollowsCommand(command, result));
    }

    [Fact]
    public async Task Restart_executes_a_command_left_pending_after_broker_ack()
    {
        var policy = CompiledPolicy.Compile(PolicyPresets.Developer,
            new Dictionary<string, PolicyDecision>());
        var adapter = FakeAgentAdapter.Success("scenario");
        var journal = new InMemoryNodeCommandJournal();
        var command = Assign(policy.RevisionId);
        var first = Runner(journal, new LocalResultOutbox(new HoldingTransport(), () => _now), adapter, policy);
        Assert.True(first.Accept(command).ShouldAckBroker);

        // Process dies here: the second runner sees the pending journal row.
        var recoveredOutbox = new LocalResultOutbox(new HoldingTransport(), () => _now);
        var restarted = Runner(journal, recoveredOutbox, adapter, policy);
        await restarted.RecoverPendingAsync(CancellationToken.None);

        Assert.Equal(1, adapter.CallCount);
        Assert.Empty(journal.Pending());
        Assert.Single(recoveredOutbox.Records);
    }

    [Fact]
    public async Task Restart_does_not_reinvoke_provider_when_result_was_saved_before_journal_completion()
    {
        var policy = CompiledPolicy.Compile(PolicyPresets.Developer,
            new Dictionary<string, PolicyDecision>());
        var adapter = FakeAgentAdapter.Success("scenario");
        var journal = new InMemoryNodeCommandJournal();
        var command = Assign(policy.RevisionId);
        var outbox = new LocalResultOutbox(new HoldingTransport(), () => _now);
        var first = Runner(journal, outbox, adapter, policy);
        Assert.True(first.Accept(command).ShouldAckBroker);

        // Models a crash after the result outbox commit but before the journal
        // completion write. The result is causally and deterministically bound
        // to the accepted command.
        outbox.Enqueue(new ResultEnvelope
        {
            MessageId = $"res-{command.MessageId}",
            SchemaVersion = "result.v1",
            MessageType = MessageTypes.ExecutionResult,
            CorrelationId = command.CorrelationId,
            CausationId = command.MessageId,
            IdempotencyKey = command.IdempotencyKey,
            WorkflowRunId = command.WorkflowRunId,
            StageRunId = command.StageRunId,
            ExecutionId = command.ExecutionId,
            AttemptId = command.AttemptId,
            AssignmentId = command.AssignmentId,
            WorkerId = command.WorkerId,
            AgentId = command.AgentId,
            LeaseEpoch = command.LeaseEpoch,
            ResultStatus = AttemptResultStatus.Succeeded,
            OutcomeSummary = "already durable",
            Traceparent = command.Traceparent,
            CreatedAt = _now,
        });

        await first.RecoverPendingAsync(CancellationToken.None);

        Assert.Equal(0, adapter.CallCount);
        Assert.Empty(journal.Pending());
        Assert.Single(outbox.Records);
    }

    [Fact]
    public async Task Provider_result_after_lease_expiry_is_fenced_to_expired()
    {
        var policy = CompiledPolicy.Compile(PolicyPresets.Developer,
            new Dictionary<string, PolicyDecision>());
        var adapter = new CallbackAdapter("scenario", _ =>
        {
            _now = _now.AddMinutes(11);
            return Task.FromResult(new AgentExecutionResult(
                true, "{\"result_status\":\"succeeded\"}", null, 0, TimeSpan.Zero));
        });
        var journal = new InMemoryNodeCommandJournal();
        var outbox = new LocalResultOutbox(new HoldingTransport(), () => _now);
        var runner = Runner(journal, outbox, adapter, policy);
        var command = Assign(policy.RevisionId);
        Assert.True(runner.Accept(command).ShouldAckBroker);

        await runner.ExecuteAcceptedAsync(command, CancellationToken.None);

        var result = Assert.Single(outbox.Records).Result;
        Assert.Equal(AttemptResultStatus.Expired, result.ResultStatus);
        Assert.Equal(FailureCategory.LeaseExpired, result.FailureCategory);
        Assert.Empty(journal.Pending());
    }

    [Fact]
    public async Task Missing_explicit_stage_and_workspace_is_a_non_retryable_schema_rejection()
    {
        var policy = CompiledPolicy.Compile(PolicyPresets.Developer,
            new Dictionary<string, PolicyDecision>());
        var adapter = FakeAgentAdapter.Success("scenario");
        var journal = new InMemoryNodeCommandJournal();
        var outbox = new LocalResultOutbox(new HoldingTransport(), () => _now);
        var runner = Runner(journal, outbox, adapter, policy);
        var command = Assign(policy.RevisionId);
        var payload = AssignmentTracker.ParseAssignPayload(command);
        command = command with
        {
            Payload = System.Text.Json.JsonSerializer.Serialize(
                new AssignCommandPayload(payload.Assignment, ProviderId: "scenario")),
        };
        Assert.True(runner.Accept(command).ShouldAckBroker);

        await runner.ExecuteAcceptedAsync(command, CancellationToken.None);

        var result = Assert.Single(outbox.Records).Result;
        Assert.Equal(AttemptResultStatus.Failed, result.ResultStatus);
        Assert.Equal(FailureCategory.SchemaRejection, result.FailureCategory);
        Assert.Equal(0, adapter.CallCount);
    }

    [Fact]
    public async Task Missing_node_local_workspace_mapping_fails_closed_before_provider_spawn()
    {
        var policy = CompiledPolicy.Compile(PolicyPresets.Developer,
            new Dictionary<string, PolicyDecision>());
        var adapter = FakeAgentAdapter.Success("scenario");
        var journal = new InMemoryNodeCommandJournal();
        var outbox = new LocalResultOutbox(new HoldingTransport(), () => _now);
        var runner = Runner(
            journal, outbox, adapter, policy,
            new SingleLocalWorkspaceResolver(
                new WorkspaceReference("other-project", "workspace", "base"), Directory.GetCurrentDirectory()));
        var command = Assign(policy.RevisionId);
        Assert.True(runner.Accept(command).ShouldAckBroker);

        await runner.ExecuteAcceptedAsync(command, CancellationToken.None);

        var result = Assert.Single(outbox.Records).Result;
        Assert.Equal(FailureCategory.SchemaRejection, result.FailureCategory);
        Assert.Equal(0, adapter.CallCount);
    }

    [Fact]
    public async Task Host_shutdown_leaves_the_accepted_command_pending_for_restart()
    {
        var policy = CompiledPolicy.Compile(PolicyPresets.Developer,
            new Dictionary<string, PolicyDecision>());
        var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var adapter = new CallbackAdapter("scenario", async ct =>
        {
            entered.TrySetResult();
            await Task.Delay(Timeout.InfiniteTimeSpan, ct);
            throw new InvalidOperationException("unreachable");
        });
        var journal = new InMemoryNodeCommandJournal();
        var outbox = new LocalResultOutbox(new HoldingTransport(), () => _now);
        var runner = Runner(journal, outbox, adapter, policy);
        var command = Assign(policy.RevisionId);
        Assert.True(runner.Accept(command).ShouldAckBroker);
        using var stopping = new CancellationTokenSource();

        var execution = runner.ExecuteAcceptedAsync(command, stopping.Token);
        await entered.Task;
        stopping.Cancel();

        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => execution);
        Assert.Single(journal.Pending());
        Assert.Empty(outbox.Records);
    }

    [Fact]
    public async Task Explicit_cancel_wins_even_when_provider_ignores_the_cancellation_token()
    {
        var policy = CompiledPolicy.Compile(PolicyPresets.Developer,
            new Dictionary<string, PolicyDecision>());
        var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var release = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var adapter = new CallbackAdapter("scenario", async _ =>
        {
            entered.TrySetResult();
            await release.Task;
            return new AgentExecutionResult(true, "provider ignored cancel", null, 0, TimeSpan.Zero);
        });
        var journal = new InMemoryNodeCommandJournal();
        var outbox = new LocalResultOutbox(new HoldingTransport(), () => _now);
        var runner = Runner(journal, outbox, adapter, policy);
        var assign = Assign(policy.RevisionId);
        Assert.True(runner.Accept(assign).ShouldAckBroker);

        var execution = runner.ExecuteAcceptedAsync(assign, CancellationToken.None);
        await entered.Task;
        var cancel = assign with
        {
            MessageId = "cmd-cancel-1",
            MessageType = MessageTypes.ExecutionCancel,
            CausationId = assign.MessageId,
            IdempotencyKey = $"{assign.AssignmentId}:cancel",
            Payload = "operator requested cancellation",
        };
        Assert.Equal(AcceptanceKind.Accepted, runner.Accept(cancel).Kind);
        await runner.ExecuteAcceptedAsync(cancel, CancellationToken.None);
        release.TrySetResult();
        await execution;

        var result = Assert.Single(outbox.Records).Result;
        Assert.Equal(AttemptResultStatus.Cancelled, result.ResultStatus);
        Assert.Equal(cancel.MessageId, result.CausationId);
        Assert.Empty(journal.Pending());
    }

    private DurableAssignmentRunner Runner(
        INodeCommandJournal journal,
        LocalResultOutbox outbox,
        IAgentAdapter adapter,
        CompiledPolicy policy,
        ILocalWorkspaceResolver? workspaces = null) => new(
        Worker,
        journal,
        new AssignmentTracker(),
        new LocalEventStore(),
        outbox,
        new AgentAdapterRegistry(new[] { adapter }, NullLogger<AgentAdapterRegistry>.Instance),
        policy,
        workspaces ?? new SingleLocalWorkspaceResolver(
            new WorkspaceReference("project", "workspace", "base"), Directory.GetCurrentDirectory()),
        () => _now);

    private CommandEnvelope Assign(string policyRevision, string taskContext = "{}")
    {
        var assignment = new Assignment(
            "asg-1", "run-1", "stage-1", "exec-1", "attempt-1", Worker, "agent.dev",
            "lease-1", 1, new[] { "development" }, _now, _now.AddMinutes(10), policyRevision);
        return new CommandEnvelope
        {
            MessageId = "cmd-1",
            SchemaVersion = "command.v1",
            MessageType = MessageTypes.ExecutionAssign,
            CorrelationId = "run-1",
            IdempotencyKey = "asg-1:attempt-1",
            WorkflowRunId = assignment.WorkflowRunId,
            StageRunId = assignment.StageRunId,
            ExecutionId = assignment.ExecutionId,
            AttemptId = assignment.AttemptId,
            AssignmentId = assignment.AssignmentId,
            WorkerId = assignment.WorkerId,
            AgentId = assignment.AgentId,
            LeaseId = assignment.LeaseId,
            LeaseEpoch = assignment.LeaseEpoch,
            IssuedAt = assignment.IssuedAt,
            ExpiresAt = assignment.ExpiresAt,
            Traceparent = Trace,
            Payload = System.Text.Json.JsonSerializer.Serialize(
                new AssignCommandPayload(
                    assignment,
                    TaskContext: taskContext,
                    ProviderId: "scenario",
                    StageType: StageType.Development,
                    NodeId: "development",
                    Workspace: new WorkspaceReference("project", "workspace", "base"))),
            PolicyRevisionId = policyRevision,
        };
    }

    private sealed class HoldingTransport : IResultTransport
    {
        public BrokerConfirm Publish(LocalOutboxRecord record) => BrokerConfirm.Confirmed;
    }

    private sealed class CallbackAdapter(
        string agentType,
        Func<CancellationToken, Task<AgentExecutionResult>> execute) : IAgentAdapter
    {
        public string AgentType { get; } = agentType;

        public Task<AgentExecutionResult> ExecuteAsync(ExecutionContext context, CancellationToken ct) =>
            execute(ct);
    }
}
