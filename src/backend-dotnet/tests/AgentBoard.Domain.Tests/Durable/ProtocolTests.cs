// SPDX-License-Identifier: MIT
using AgentBoard.Contracts;
using AgentBoard.Domain.Workflow.Durable;
using Xunit;

namespace AgentBoard.Domain.Tests.Durable;

/// <summary>
/// A1 protocol mechanics: dispatch, result intake with dedup and fencing,
/// retry-or-DLQ, outbox dispatch against the broker, approvals, and capture
/// or restore of the whole plane (doc 150 PR-006..PR-008, PR-012;
/// doc 151 §5.6, §6).
/// </summary>
public class ProtocolTests
{
    // ------------------------------------------------------------------
    // Dispatch and transactional outbox
    // ------------------------------------------------------------------

    [Fact]
    public void Dispatch_creates_assignment_attempt_and_durable_command_together()
    {
        var fixture = new PlaneFixture();
        var assignment = fixture.DispatchDev();

        Assert.Equal(1, assignment.LeaseEpoch);
        Assert.NotNull(fixture.Plane.Registry.RequireAttempt(assignment.AttemptId));
        Assert.Single(fixture.Plane.Outbox.Messages);
        Assert.Equal(OutboxState.Pending, fixture.Plane.Outbox.Messages.Single().State);
    }

    [Fact]
    public void Dispatcher_confirms_publish_and_never_reinvents_messages()
    {
        var fixture = new PlaneFixture();
        fixture.DispatchDev();

        var transport = new FakeTransport();
        var dispatcher = new OutboxDispatcher(
            fixture.Plane.Outbox, transport, fixture.Plane.Planner, fixture.Plane.DeadLetters, () => fixture.Now);

        Assert.Equal(1, dispatcher.DispatchDue());
        Assert.Equal(OutboxState.Confirmed, fixture.Plane.Outbox.Messages.Single().State);
        Assert.Equal(1, transport.PublishCount);

        // Confirmed messages are terminal; a later pass does not republish.
        Assert.Equal(0, dispatcher.DispatchDue());
        Assert.Equal(1, transport.PublishCount);
    }

    [Fact]
    public void Stale_broker_completion_cannot_confirm_a_recovered_outbox_attempt()
    {
        var fixture = new PlaneFixture();
        fixture.DispatchDev();
        var message = Assert.Single(fixture.Plane.Outbox.Messages);
        var first = fixture.Plane.Outbox.BeginDispatch(
            message.MessageId, fixture.Now, TimeSpan.FromSeconds(1));

        fixture.Advance(1);
        var recovered = fixture.Plane.Outbox.BeginDispatch(
            message.MessageId, fixture.Now, TimeSpan.FromSeconds(1));

        var stale = fixture.Plane.Outbox.CompleteDispatch(
            first,
            PublishResult.Confirmed,
            fixture.Now,
            fixture.Plane.Planner,
            fixture.Plane.DeadLetters);
        Assert.Equal(OutboxState.Published, stale);
        Assert.Equal(recovered.AttemptCount, fixture.Plane.Outbox.Require(message.MessageId).AttemptCount);

        var current = fixture.Plane.Outbox.CompleteDispatch(
            recovered,
            PublishResult.Confirmed,
            fixture.Now,
            fixture.Plane.Planner,
            fixture.Plane.DeadLetters);
        Assert.Equal(OutboxState.Confirmed, current);
    }

    [Fact]
    public void Publish_confirm_timeout_backs_off_then_dead_letters()
    {
        var fixture = new PlaneFixture();
        fixture.DispatchDev();

        var transport = new FakeTransport(alwaysFail: true);
        var planner = new RetryPlanner(_ => new RetryPolicy(2, TimeSpan.FromSeconds(10), TimeSpan.FromMinutes(1)));
        var dispatcher = new OutboxDispatcher(
            fixture.Plane.Outbox, transport, planner, fixture.Plane.DeadLetters, () => fixture.Now);

        for (var round = 1; ; round++)
        {
            fixture.Advance(1);
            dispatcher.DispatchDue();
            var state = fixture.Plane.Outbox.Messages.Single().State;
            if (state == OutboxState.DeadLettered)
            {
                Assert.True(round >= 3, "dead-lettering must not happen before the retry budget is spent");
                break;
            }

            Assert.Equal(OutboxState.Pending, state);
            Assert.True(round < 5, "messages must eventually reach a terminal state");
        }

        Assert.Single(fixture.Plane.DeadLetters.Entries);
        Assert.Equal(FailureCategory.TransportFailure, fixture.Plane.DeadLetters.Entries.Single().Category);
    }

    // ------------------------------------------------------------------
    // Result intake: duplicates, ordering, fencing
    // ------------------------------------------------------------------

    [Fact]
    public void Duplicate_message_is_answered_from_the_inbox_without_side_effects()
    {
        var fixture = new PlaneFixture();
        fixture.DispatchDev();
        var result = fixture.Result(AttemptResultStatus.Succeeded, summary: "dev done");

        var first = fixture.Plane.Results.Process(result);
        var second = fixture.Plane.Results.Process(result);

        Assert.Equal(ResultOutcomeKind.Accepted, first.Kind);
        Assert.Equal(ResultOutcomeKind.Duplicate, second.Kind);

        var execution = fixture.Plane.Registry.RequireExecution(fixture.ExecutionId);
        Assert.Single(execution.Attempts); // no phantom second attempt
    }

    [Fact]
    public void Same_idempotency_key_different_message_is_still_one_outcome()
    {
        var fixture = new PlaneFixture();
        fixture.DispatchDev();

        var assignment = fixture.CurrentAssignment!;
        var key = $"{assignment.AssignmentId}:{assignment.AttemptId}";

        Assert.Equal(ResultOutcomeKind.Accepted,
            fixture.Plane.Results.Process(fixture.Result(AttemptResultStatus.Succeeded, idempotencyKey: key)).Kind);
        Assert.Equal(ResultOutcomeKind.Duplicate,
            fixture.Plane.Results.Process(fixture.Result(AttemptResultStatus.Succeeded, idempotencyKey: key)).Kind);
    }

    [Fact]
    public void Stale_epoch_result_is_rejected_and_cannot_outcome_the_execution()
    {
        var fixture = new PlaneFixture();
        fixture.DispatchDev();

        // A second assignment supersedes the first lease (epoch 2 current).
        var retry = fixture.Plane.Dispatcher.Dispatch(
            fixture.ExecutionId, "worker-9", "agent.dev", new[] { "development" }, "policy-rev-1",
            TimeSpan.FromMinutes(10));
        Assert.Equal(2, retry.LeaseEpoch);

        var stale = fixture.Plane.Results.Process(
            fixture.Result(AttemptResultStatus.Succeeded, assignmentId: retry.AssignmentId, leaseEpoch: 1));

        Assert.Equal(ResultOutcomeKind.RejectedStaleEpoch, stale.Kind);
        Assert.Null(fixture.Plane.Registry.RequireExecution(fixture.ExecutionId).Outcome);

        // The new epoch's own result is accepted normally.
        fixture.CurrentAssignment = retry;
        var fresh = fixture.Plane.Results.Process(
            fixture.Result(AttemptResultStatus.Succeeded, summary: "from epoch 2"));
        Assert.Equal(ResultOutcomeKind.Accepted, fresh.Kind);
    }

    [Fact]
    public void Expired_lease_result_is_rejected_attempt_marked_expired()
    {
        var fixture = new PlaneFixture();
        var assignment = fixture.DispatchDev();

        fixture.Advance(11); // lease budget was 10 minutes

        var verdict = fixture.Plane.Results.Process(
            fixture.Result(AttemptResultStatus.Succeeded, summary: "late"));

        Assert.Equal(ResultOutcomeKind.RejectedLeaseExpired, verdict.Kind);
        Assert.Equal(ExecutionAttemptState.Expired,
            fixture.Plane.Registry.RequireAttempt(assignment.AttemptId).Current.State);
        Assert.Null(fixture.Plane.Registry.RequireExecution(fixture.ExecutionId).Outcome);
    }

    [Fact]
    public void Renewal_extends_live_lease_but_never_a_dead_one()
    {
        var fixture = new PlaneFixture();
        var assignment = fixture.DispatchDev();

        var renewed = fixture.Plane.Leases.Renew(assignment.AssignmentId, fixture.Now + TimeSpan.FromMinutes(30));
        Assert.Equal(assignment.LeaseEpoch, renewed.LeaseEpoch);

        fixture.Advance(45);
        Assert.Throws<Domain.Common.InvalidValueException>(() =>
            fixture.Plane.Leases.Renew(assignment.AssignmentId, fixture.Now + TimeSpan.FromMinutes(10)));
    }

    [Fact]
    public void Zero_lease_budget_is_refused_before_any_mutation()
    {
        var fixture = new PlaneFixture();

        Assert.Throws<AgentBoard.Domain.Common.InvalidValueException>(() => fixture.Plane.Dispatcher.Dispatch(
            fixture.ExecutionId, "worker-1", "agent.dev", new[] { "development" }, "policy-rev-1",
            TimeSpan.Zero));

        // The stage must be untouched: an invalid dispatch may not even
        // leave the Pending -> Assigned step behind (doc 151 §6.1 one unit).
        Assert.Equal(StageRunState.Pending, fixture.Plane.Registry.RequireStage("stg-dev-1").Current.State);
        Assert.Empty(fixture.Plane.Leases.Assignments);
        Assert.Empty(fixture.Plane.Outbox.Messages);
    }

    [Fact]
    public void Summary_traffic_cannot_finalize_the_authoritative_machines()
    {
        var fixture = new PlaneFixture();
        fixture.DispatchDev();

        var summary = fixture.Result(AttemptResultStatus.Succeeded, summary: "progress") with
        {
            MessageType = MessageTypes.ExecutionSummary,
        };

        var verdict = fixture.Plane.Results.Process(summary);
        Assert.Equal(ResultOutcomeKind.RejectedNonAuthoritative, verdict.Kind);

        var attempt = fixture.Plane.Registry.RequireExecution(fixture.ExecutionId).LatestAttempt!;
        Assert.Equal(ExecutionAttemptState.Created, attempt.Current.State);
        Assert.Null(fixture.Plane.Registry.RequireExecution(fixture.ExecutionId).Outcome);
        Assert.Contains(fixture.Plane.Registry.Audit.Records, r => r.Action == "summary.received");
    }

    [Fact]
    public void Result_without_provable_origin_fails_closed()
    {
        var fixture = new PlaneFixture();
        fixture.CompleteDevelopment();

        // A second execution+assignment recorded directly in the lease store
        // (as restored or partially committed state might present) has no
        // issued command behind it. The result must be refused, not trusted.
        fixture.Plane.Registry.AddStage("run-1", "stg-rev-unproven", StageType.Review, 1, null);
        fixture.Plane.Registry.AddExecution("stg-rev-unproven", "exec-2");
        var assignment = new Assignment(
            "asg-unproven", "run-1", "stg-rev-unproven", "exec-2", "att-unproven", "worker-1", "agent.rev",
            "lease-x", 1, new[] { "review" }, fixture.Now, fixture.Now + TimeSpan.FromMinutes(10),
            "policy-rev-1");
        fixture.Plane.Leases.Grant(assignment);
        fixture.Plane.Registry.AddAttempt("exec-2", "att-unproven", 1);

        var verdict = fixture.Plane.Results.Process(new ResultEnvelope
        {
            MessageId = "msg-unproven",
            SchemaVersion = "result.v1",
            MessageType = MessageTypes.ExecutionResult,
            CorrelationId = "run-1",
            CausationId = "cmd-never-issued",
            IdempotencyKey = "asg-unproven:att-unproven",
            WorkflowRunId = "run-1",
            StageRunId = "stg-rev-unproven",
            ExecutionId = "exec-2",
            AttemptId = "att-unproven",
            AssignmentId = "asg-unproven",
            WorkerId = "worker-1",
            AgentId = "agent.rev",
            LeaseEpoch = 1,
            ResultStatus = AttemptResultStatus.Succeeded,
            Traceparent = PlaneFixture.Trace,
            CreatedAt = fixture.Now,
        });

        Assert.Equal(ResultOutcomeKind.RejectedSchema, verdict.Kind);
        Assert.Contains("cannot be verified", verdict.Reason);
    }

    // ------------------------------------------------------------------
    // Failure handling: retry vs DLQ
    // ------------------------------------------------------------------

    [Fact]
    public void Retryable_failure_waits_for_its_backoff_then_dispatches_at_new_epoch()
    {
        var fixture = new PlaneFixture();
        fixture.DispatchDev();
        var firstCommandCount = fixture.Plane.Outbox.Messages.Count;

        var verdict = fixture.Plane.Results.Process(
            fixture.Result(AttemptResultStatus.Failed, FailureCategory.ProviderFailure));

        Assert.Equal(ResultOutcomeKind.Accepted, verdict.Kind);
        Assert.Null(verdict.NewAttemptId);

        // The retry is scheduled, not fired: hammering the provider on every
        // transient failure is exactly what PR-012's backoff bound forbids.
        var execution = fixture.Plane.Registry.RequireExecution(fixture.ExecutionId);
        Assert.Single(execution.Attempts);
        Assert.Equal(firstCommandCount, fixture.Plane.Outbox.Messages.Count);
        Assert.Single(fixture.Plane.Retries.Pending);

        fixture.Advance(1); // default base delay is 2 seconds

        // A dispatch that throws must NOT consume the retry: losing the only
        // deferred record would turn the backoff into a work-loss window.
        Assert.Single(fixture.Plane.Retries.Pending);
        Assert.Equal(1, fixture.Plane.ProcessDueRetries());

        Assert.Equal(2, execution.Attempts.Count);
        Assert.Equal(2, execution.Attempts[1].Current.LeaseEpoch);
        Assert.True(fixture.Plane.Outbox.Messages.Count > firstCommandCount);
        Assert.Null(execution.Outcome);
        Assert.Empty(fixture.Plane.Retries.Pending);
    }

    [Fact]
    public void Failed_retry_dispatch_keeps_the_retry_on_record()
    {
        var fixture = new PlaneFixture();
        fixture.DispatchDev();
        fixture.Plane.Results.Process(
            fixture.Result(AttemptResultStatus.Failed, FailureCategory.ProviderFailure));
        fixture.Advance(1);

        // Make the retry impossible to dispatch: cancel the stage while the
        // backoff sleeps, so the due dispatch refuses. The queue must retain
        // the entry instead of having consumed it first.
        fixture.Plane.Registry.MoveStage("stg-dev-1", StageRunState.Cancelled,
            PlaneFixture.Ctx("operator cancelled while retry was deferred"));

        Assert.Throws<AgentBoard.Domain.Common.InvalidValueException>(
            () => fixture.Plane.ProcessDueRetries());
        Assert.Single(fixture.Plane.Retries.Pending); // still there, nothing lost
    }

    [Fact]
    public void Result_not_following_its_issued_command_is_rejected()
    {
        var fixture = new PlaneFixture();
        fixture.DispatchDev();

        var command = fixture.Plane.Sent.Commands.Single();

        // Right assignment, wrong causation: a self-consistent fabrication
        // must not masquerade as the answer to the command actually sent.
        var fabricated = fixture.Result(AttemptResultStatus.Succeeded, summary: "sneaky") with
        {
            CausationId = "cmd-that-was-never-sent",
            IdempotencyKey = command.IdempotencyKey + "-altered",
        };

        var verdict = fixture.Plane.Results.Process(fabricated);
        Assert.Equal(ResultOutcomeKind.RejectedSchema, verdict.Kind);
        Assert.Null(fixture.Plane.Registry.RequireExecution(fixture.ExecutionId).Outcome);
    }

    [Fact]
    public void Cancellation_result_must_follow_the_cancel_command_and_never_enters_the_dlq()
    {
        var fixture = new PlaneFixture();
        var assignment = fixture.DispatchDev();
        fixture.Plane.Dispatcher.DispatchCancel(fixture.ExecutionId, "operator requested stop");
        var cancel = fixture.Plane.Sent.Commands.Single(c => c.MessageType == MessageTypes.ExecutionCancel);

        var result = fixture.Result(AttemptResultStatus.Cancelled) with
        {
            CausationId = cancel.MessageId,
            IdempotencyKey = cancel.IdempotencyKey,
        };
        var verdict = fixture.Plane.Results.Process(result);

        Assert.Equal(ResultOutcomeKind.Accepted, verdict.Kind);
        Assert.Equal(StageRunState.Cancelled, fixture.Plane.Registry.RequireStage(fixture.StageId).Current.State);
        Assert.Equal(ExecutionAttemptState.Cancelled,
            fixture.Plane.Registry.RequireAttempt(assignment.AttemptId).Current.State);
        Assert.Empty(fixture.Plane.DeadLetters.Entries);
        Assert.Null(fixture.Plane.Registry.RequireExecution(fixture.ExecutionId).Outcome);
    }

    [Fact]
    public void CommitAtomic_rolls_the_plane_back_when_persistence_fails()
    {
        var fixture = new PlaneFixture();
        fixture.DispatchDev();
        var result = fixture.Result(AttemptResultStatus.Succeeded, summary: "dev done");

        Assert.Throws<IOException>(() => fixture.Plane.CommitAtomic(new FailingCommitter(), () =>
        {
            fixture.Plane.Results.Process(result);
            Assert.NotNull(fixture.Plane.Registry.RequireExecution(fixture.ExecutionId).Outcome); // applied in memory
        }));

        // ...and after the failed commit the plane is back at the last durable
        // point: no outcome, and the redelivered result can be accepted once.
        Assert.Null(fixture.Plane.Registry.RequireExecution(fixture.ExecutionId).Outcome);
        Assert.Equal(ResultOutcomeKind.Accepted, fixture.Plane.Results.Process(result).Kind);
        Assert.NotNull(fixture.Plane.Registry.RequireExecution(fixture.ExecutionId).Outcome);
    }

    private sealed class FailingCommitter : AgentBoard.Domain.Workflow.Durable.IPlaneCommitter
    {
        public void Commit(PlaneState state) => throw new IOException("simulated disk failure at commit");
    }

    [Fact]
    public void Non_retryable_failure_dead_letters_and_fails_the_stage()
    {
        var fixture = new PlaneFixture();
        fixture.DispatchDev();

        var verdict = fixture.Plane.Results.Process(
            fixture.Result(AttemptResultStatus.Failed, FailureCategory.PolicyDenied));

        Assert.Equal(ResultOutcomeKind.Accepted, verdict.Kind);
        Assert.Null(verdict.NewAttemptId);
        Assert.NotNull(verdict.DeadLetterId);
        Assert.Equal(StageRunState.Failed, fixture.Plane.Registry.RequireStage(fixture.StageId).Current.State);

        var quarantined = fixture.Plane.DeadLetters.Quarantined().Single();
        var resolved = fixture.Plane.DeadLetters.Resolve(quarantined.Id, DeadLetterState.Abandoned, fixture.Now, "operator");
        Assert.Equal(DeadLetterState.Abandoned, resolved.State);
        Assert.Empty(fixture.Plane.DeadLetters.Quarantined());
    }

    [Fact]
    public void Retry_budget_exhaustion_ends_in_queryable_dead_letter()
    {
        var fixture = new PlaneFixture();
        var planner = new RetryPlanner(_ => new RetryPolicy(1, TimeSpan.FromSeconds(1), TimeSpan.FromSeconds(1)));

        var tight = new DurableServerPlane(() => fixture.Now, fixture.NextId, planner);
        tight.Registry.PublishVersion(fixture.Version);
        tight.Registry.CreateRun("run-1", fixture.Version.VersionId);
        tight.Registry.MoveRun("run-1", WorkflowRunState.Queued, PlaneFixture.Ctx("q"));
        tight.Registry.MoveRun("run-1", WorkflowRunState.Running, PlaneFixture.Ctx("r"));
        tight.Registry.AddStage("run-1", "stg-dev-1", StageType.Development, 1, null);
        tight.Registry.AddExecution("stg-dev-1", "exec-dev-1");
        tight.Dispatcher.Dispatch("exec-dev-1", "worker-1", "agent.dev", new[] { "development" }, "policy-rev-1",
            TimeSpan.FromMinutes(10));

        for (var i = 0; i < 3; i++)
        {
            var current = tight.Leases.CurrentFor("exec-dev-1")!;
            tight.Dispatcher.GetType(); // no renewal needed: fresh leases each round

            var command = tight.Sent.TryGet(current.AssignmentId, out var issued) ? issued : null;
            Assert.NotNull(command);

            var envelope = new ResultEnvelope
            {
                MessageId = $"msg-{i}",
                SchemaVersion = "result.v1",
                MessageType = MessageTypes.ExecutionResult,
                CorrelationId = "run-1",
                IdempotencyKey = command!.IdempotencyKey,
                CausationId = command.MessageId,
                WorkflowRunId = "run-1",
                StageRunId = "stg-dev-1",
                ExecutionId = "exec-dev-1",
                AttemptId = current.AttemptId,
                AssignmentId = current.AssignmentId,
                WorkerId = current.WorkerId,
                AgentId = current.AgentId,
                LeaseEpoch = current.LeaseEpoch,
                ResultStatus = AttemptResultStatus.Failed,
                FailureCategory = FailureCategory.ProviderFailure,
                Traceparent = PlaneFixture.Trace,
                CreatedAt = fixture.Now,
            };

            var verdict = tight.Results.Process(envelope);
            Assert.Equal(ResultOutcomeKind.Accepted, verdict.Kind);

            if (verdict.DeadLetterId is not null)
            {
                Assert.Single(tight.DeadLetters.Entries);
                Assert.Equal(StageRunState.Failed, tight.Registry.RequireStage("stg-dev-1").Current.State);
                return;
            }

            // Retryable round: fire the deferred retry to keep the chain going.
            fixture.Advance(1);
            Assert.Equal(1, tight.ProcessDueRetries());
        }

        Assert.Fail("retry budget of 1 should have dead-lettered within three failures");
    }

    // ------------------------------------------------------------------
    // Explicit stage handoff (doc 150 PR-010, doc 151 §7)
    // ------------------------------------------------------------------

    [Fact]
    public void IssueHandoff_carries_accepted_evidence_into_the_next_stage_contract()
    {
        var fixture = new PlaneFixture();
        fixture.DispatchDev();
        var digest = new string('a', 64);

        var result = fixture.Result(AttemptResultStatus.Succeeded, summary: "dev done") with
        {
            ArtifactReferences = new[] { new ArtifactReference("artifact://diff", digest, 12) },
            CommitOrVersion = "commit-1",
            TestEvidence = new[] { "48 tests green" },
        };
        Assert.Equal(ResultOutcomeKind.Accepted, fixture.Plane.Results.Process(result).Kind);

        var handoff = fixture.Plane.IssueHandoff("stg-dev-1", fixture.ExecutionId, StageType.Review,
            new[] { "review" }, new WorkspaceReference("proj", "ws", "commit-1"));

        Assert.Equal("handoff.v1", handoff.ContextVersion);
        Assert.Equal("commit-1", handoff.CommitOrVersion);
        Assert.Single(handoff.ArtifactReferences);
        Assert.Single(handoff.TestEvidence);
        var outcome = fixture.Plane.Registry.RequireExecution(fixture.ExecutionId).Outcome!;
        Assert.Equal(outcome.OutcomeId, handoff.SourceOutcomeId);

        // The next stage's command names the handoff; the target depends only
        // on this context, never on the previous provider session.
        fixture.Plane.Registry.AddStage("run-1", "stg-rev-1", StageType.Review, 1, null);
        fixture.Plane.Registry.AddExecution("stg-rev-1", "exec-rev");
        fixture.Plane.Dispatcher.Dispatch("exec-rev", "worker-2", "agent.rev", new[] { "review" },
            "policy-rev-1", TimeSpan.FromMinutes(10), handoffId: handoff.HandoffId);

        var command = fixture.Plane.Sent.Commands.Last();
        var payload = System.Text.Json.JsonSerializer.Deserialize<AssignCommandPayload>(command.Payload)!;
        Assert.Equal(handoff.HandoffId, payload.HandoffId);
        Assert.Equal("exec-rev", payload.Assignment.ExecutionId);

        // Durable across restart like everything else on the plane.
        var state = fixture.Plane.Capture();
        var revived = DurableServerPlane.Restore(() => fixture.Now, fixture.NextId, state);
        Assert.Equal("commit-1", revived.Handoffs.Require(handoff.HandoffId).CommitOrVersion);
    }

    [Fact]
    public void IssueHandoff_requires_an_accepted_outcome_first()
    {
        var fixture = new PlaneFixture();
        fixture.DispatchDev();

        Assert.Throws<AgentBoard.Domain.Common.InvalidValueException>(() =>
            fixture.Plane.IssueHandoff("stg-dev-1", fixture.ExecutionId, StageType.Review,
                new[] { "review" }, new WorkspaceReference("p", "w", "v")));
    }

    [Fact]
    public void Handoff_without_capabilities_fails_closed_at_the_registry()
    {
        var fixture = new PlaneFixture();
        fixture.CompleteDevelopment();

        Assert.Throws<AgentBoard.Domain.Common.InvalidValueException>(() =>
            fixture.Plane.IssueHandoff("stg-dev-1", fixture.ExecutionId, StageType.Review,
                Array.Empty<string>(), new WorkspaceReference("p", "w", "v")));
    }

    [Fact]
    public void Handoff_source_stage_must_match_the_resolved_execution()
    {
        var fixture = new PlaneFixture();
        fixture.CompleteDevelopment();

        Assert.Throws<AgentBoard.Domain.Common.InvalidValueException>(() =>
            fixture.Plane.IssueHandoff("stg-from-another-run", fixture.ExecutionId, StageType.Review,
                new[] { "review" }, new WorkspaceReference("p", "w", "v")));
    }

    [Fact]
    public void Handoff_workspace_version_must_match_accepted_evidence()
    {
        var fixture = new PlaneFixture();
        fixture.DispatchDev();
        var result = fixture.Result(AttemptResultStatus.Succeeded, summary: "dev done") with
        {
            CommitOrVersion = "commit-good",
        };
        Assert.Equal(ResultOutcomeKind.Accepted, fixture.Plane.Results.Process(result).Kind);

        Assert.Throws<AgentBoard.Domain.Common.InvalidValueException>(() =>
            fixture.Plane.IssueHandoff("stg-dev-1", fixture.ExecutionId, StageType.Review,
                new[] { "review" }, new WorkspaceReference("p", "w", "commit-wrong")));
    }

    [Fact]
    public void Handoff_is_deeply_frozen_and_cannot_be_dispatched_to_the_wrong_stage_type()
    {
        var fixture = new PlaneFixture();
        fixture.CompleteDevelopment();
        var capabilities = new List<string> { "review" };
        var handoff = fixture.Plane.IssueHandoff("stg-dev-1", fixture.ExecutionId, StageType.Review,
            capabilities, new WorkspaceReference("p", "w", "v"));

        capabilities[0] = "qa";
        Assert.Equal("review", Assert.Single(handoff.RequiredCapabilities));
        Assert.Throws<NotSupportedException>(() =>
            ((IList<string>)handoff.RequiredCapabilities)[0] = "qa");

        var wrongHandoff = fixture.Plane.IssueHandoff(
            "stg-dev-1", fixture.ExecutionId, StageType.Qa,
            new[] { "qa" }, new WorkspaceReference("p", "w", "v"));
        fixture.Plane.Registry.AddStage("run-1", "stg-review-1", StageType.Review, 1, null);
        fixture.Plane.Registry.AddExecution("stg-review-1", "exec-review");
        Assert.Throws<AgentBoard.Domain.Common.InvalidValueException>(() =>
            fixture.Plane.Dispatcher.Dispatch("exec-review", "worker-2", "agent.qa", new[] { "qa" },
                "policy-rev-1", TimeSpan.FromMinutes(10), wrongHandoff.HandoffId));
    }

    // ------------------------------------------------------------------
    // Approvals
    // ------------------------------------------------------------------

    [Fact]
    public void Approval_window_expiry_moves_pending_to_expired_with_audit()
    {
        var fixture = new PlaneFixture();
        fixture.DispatchDev();

        fixture.Plane.Approvals.Open("apr-1", fixture.StageId,
            fixture.CurrentAssignment!.AssignmentId, "policy-rev-1", PolicyActionKinds.GitPush,
            TimeSpan.FromMinutes(5));

        fixture.Advance(6);
        Assert.Equal(1, fixture.Plane.Approvals.ExpireStale());
        Assert.Equal(ApprovalState.Expired, fixture.Plane.Approvals.Require("apr-1").State);
        Assert.Contains(fixture.Plane.Registry.Audit.Records,
            a => a.Action == "approval.expired" && a.SubjectId == "apr-1");
    }

    [Fact]
    public void Granted_approval_is_audited_with_actor_and_reason()
    {
        var fixture = new PlaneFixture();
        fixture.DispatchDev();
        fixture.Plane.Approvals.Open("apr-2", fixture.StageId,
            fixture.CurrentAssignment!.AssignmentId, "policy-rev-1", PolicyActionKinds.GitPush,
            TimeSpan.FromMinutes(5));

        fixture.Plane.Approvals.Decide("apr-2", granted: true, actor: "operator-jason", reason: "lgtm");

        Assert.True(fixture.Plane.Approvals.IsGranted("apr-2"));
        var record = fixture.Plane.Registry.Audit.Records.Single(a => a.Action == "approval.granted");
        Assert.Equal("operator-jason", record.Actor);
        Assert.Equal("lgtm", record.Reason);
    }

    [Fact]
    public void Server_grant_is_bound_to_the_full_policy_decision_context()
    {
        var fixture = new PlaneFixture();
        var assignment = fixture.DispatchDev();
        var decision = new PolicyDecisionRequest(
            new PolicyAction(PolicyActionKinds.GitCommit, "/repo/a.cs"),
            assignment.AgentId, assignment.RequiredCapabilities, StageType.Development,
            assignment.WorkflowRunId, new WorkspaceReference("p", "w", "commit-1"),
            assignment.PolicyRevisionId, ApprovalGranted: false, ApprovalChannelOpen: true);

        var request = fixture.Plane.AwaitApproval(
            fixture.StageId, assignment.AssignmentId, decision, TimeSpan.FromMinutes(5));
        fixture.Plane.ResolveApproval(request.ApprovalId, granted: true,
            actor: "operator-jason", reason: "reviewed exact command");
        var grant = fixture.Plane.Approvals.Grant(request.ApprovalId);

        Assert.Equal("/repo/a.cs", grant.Resource);
        Assert.Equal(assignment.AgentId, grant.AgentId);
        Assert.Equal("commit-1", grant.WorkspaceBaseVersion);
        Assert.Equal("operator-jason", grant.GrantedBy);
    }

    [Fact]
    public void Late_grant_expires_the_approval_and_fails_the_parked_stage()
    {
        var fixture = new PlaneFixture();
        var assignment = fixture.DispatchDev();
        var decision = new PolicyDecisionRequest(
            new PolicyAction(PolicyActionKinds.GitCommit, "/repo/a.cs"),
            assignment.AgentId, assignment.RequiredCapabilities, StageType.Development,
            assignment.WorkflowRunId, new WorkspaceReference("p", "w", "commit-1"),
            assignment.PolicyRevisionId, ApprovalGranted: false, ApprovalChannelOpen: true);
        var request = fixture.Plane.AwaitApproval(
            fixture.StageId, assignment.AssignmentId, decision, TimeSpan.FromMinutes(5));
        fixture.Advance(6);

        var stage = fixture.Plane.ResolveApproval(request.ApprovalId, granted: true,
            actor: "operator-late", reason: "too late");

        Assert.Equal(ApprovalState.Expired,
            fixture.Plane.Approvals.Require(request.ApprovalId).State);
        Assert.Equal(StageRunState.Failed, stage.State);
        Assert.False(fixture.Plane.Approvals.IsGranted(request.ApprovalId));
        Assert.Throws<AgentBoard.Domain.Common.InvalidValueException>(
            () => fixture.Plane.Approvals.Grant(request.ApprovalId));
    }

    // ------------------------------------------------------------------
    // Recovery: capture and restore (doc 150 NFR-005, PR-002)
    // ------------------------------------------------------------------

    [Fact]
    public void Restored_plane_keeps_outcomes_dedup_state_and_leases()
    {
        var fixture = new PlaneFixture();
        var assignment = fixture.DispatchDev();
        var result = fixture.Result(AttemptResultStatus.Succeeded, summary: "dev done");
        fixture.Plane.Results.Process(result);

        var state = fixture.Plane.Capture();

        // Simulate the process dying and coming back with the persisted state.
        var revived = DurableServerPlane.Restore(() => fixture.Now, fixture.NextId, state);

        var snapshot = revived.Registry.Snapshot("run-1")!;
        var execution = snapshot.Stages.Single().Executions.Single();
        Assert.NotNull(execution.Outcome);
        Assert.Equal(StageRunState.Succeeded, snapshot.Stages.Single().Stage.State);

        // Duplicate redelivery of the same message after restart stays a duplicate.
        var duplicate = revived.Results.Process(result);
        Assert.Equal(ResultOutcomeKind.Duplicate, duplicate.Kind);
        Assert.Single(revived.Registry.RequireExecution(fixture.ExecutionId).Attempts);

        // Lease bookkeeping survived: the assignment is still known at its epoch.
        Assert.Equal(LeaseVerdict.Valid, revived.Leases.Check(assignment.AssignmentId, assignment.LeaseEpoch));
    }

    [Fact]
    public void Restored_plane_still_refuses_a_second_outcome_for_the_execution()
    {
        var fixture = new PlaneFixture();
        fixture.CompleteDevelopment();
        var state = fixture.Plane.Capture();

        var revived = DurableServerPlane.Restore(() => fixture.Now, fixture.NextId, state);

        Assert.NotNull(revived.Registry.RequireExecution(fixture.ExecutionId).Outcome);
        Assert.Throws<Domain.Common.DuplicateException>(() => revived.Registry.AcceptOutcome(
            fixture.ExecutionId,
            new Outcome("out-x", fixture.ExecutionId,
                revived.Registry.RequireExecution(fixture.ExecutionId).Attempts[0].Current.AttemptId, fixture.Now)));
    }

    private sealed class FakeTransport : ICommandTransport
    {
        private readonly bool _alwaysFail;

        public FakeTransport(bool alwaysFail = false) => _alwaysFail = alwaysFail;

        public int PublishCount { get; private set; }

        public PublishResult Publish(OutboxMessage message)
        {
            PublishCount++;
            return _alwaysFail ? PublishResult.Failed : PublishResult.Confirmed;
        }
    }
}
