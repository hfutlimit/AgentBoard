// SPDX-License-Identifier: MIT
using AgentBoard.Contracts;
using AgentBoard.Domain.Common;

namespace AgentBoard.Domain.Workflow.Durable;

/// <summary>
/// Outcome of the Server's six-step result intake
/// (doc 151 §5.6). Every non-accepted verdict is still a durable, auditable
/// answer — dropping a message silently is not one of the options.
/// </summary>
public enum ResultOutcomeKind
{
    Accepted,
    Duplicate,
    RejectedSchema,
    RejectedUnknownAssignment,
    RejectedStaleEpoch,
    RejectedLeaseExpired,
    RejectedIllegalTransition,

    /// <summary>Best-effort summary traffic; recorded but never authoritative.</summary>
    RejectedNonAuthoritative,
}

/// <summary>
/// The recorded answer for one result message. Duplicates receive this same
/// verdict — that is how a redelivery is answered without re-running the
/// business effect (doc 150 PR-007).
/// </summary>
public sealed record ResultVerdict(
    ResultOutcomeKind Kind,
    string Reason,
    string? ExecutionId = null,
    StageRun? CreatedIteration = null,
    string? NewAttemptId = null,
    string? DeadLetterId = null);

/// <summary>
/// Issues execution commands: grants a lease at a new epoch, creates the
/// attempt it belongs to, and enqueues the durable command — all in one call
/// so a half-applied assignment cannot exist (doc 151 §6.1: state update and
/// outbox row commit together).
/// </summary>
public sealed class CommandDispatcher
{
    private readonly WorkflowRegistry _registry;
    private readonly LeaseRegistry _leases;
    private readonly ServerOutbox _outbox;
    private readonly Func<DateTimeOffset> _clock;
    private readonly Func<string> _nextId;
    private readonly SentCommandLog _sent;

    public CommandDispatcher(
        WorkflowRegistry registry,
        LeaseRegistry leases,
        ServerOutbox outbox,
        Func<DateTimeOffset> clock,
        Func<string> nextId,
        SentCommandLog sent)
    {
        _registry = registry;
        _leases = leases;
        _outbox = outbox;
        _clock = clock;
        _nextId = nextId;
        _sent = sent;
    }

    public Assignment Dispatch(
        string executionId,
        string workerId,
        string agentId,
        IReadOnlyList<string> requiredCapabilities,
        string policyRevisionId,
        TimeSpan leaseBudget,
        string? handoffId = null)
    {
        var execution = _registry.RequireExecution(executionId);
        var stage = _registry.RequireStage(execution.Current.StageRunId);
        var run = _registry.RequireRun(stage.Current.RunId);

        if (RunTransitions.IsTerminal(stage.Current.State))
        {
            throw new InvalidValueException($"stage '{stage.Current.StageRunId}' is {stage.Current.State}; nothing to dispatch");
        }

        // Every check that can fail runs BEFORE the first mutation, so an
        // invalid dispatch cannot leave a half-applied (stage assigned / lease
        // granted / attempt missing) trail behind it.
        if (leaseBudget <= TimeSpan.Zero)
        {
            throw new InvalidValueException("a dispatch needs a positive lease budget");
        }

        if (execution.Outcome is not null)
        {
            throw new InvalidValueException(
                $"execution '{executionId}' already resolved; dispatching again would court a second outcome");
        }

        var mustAssignStage = stage.Machine.Current == StageRunState.Pending;

        if (mustAssignStage)
        {
            _registry.MoveStage(stage.Current.StageRunId, StageRunState.Assigned,
                new TransitionContext("server", "assignment dispatched", SchemaVersions.Registry));
        }

        var now = _clock();
        var epoch = _leases.NextEpoch(executionId);
        var attemptId = $"att-{_nextId()}";
        var assignment = new Assignment(
            AssignmentId: $"asg-{_nextId()}",
            WorkflowRunId: run.Current.RunId,
            StageRunId: stage.Current.StageRunId,
            ExecutionId: executionId,
            AttemptId: attemptId,
            WorkerId: workerId,
            AgentId: agentId,
            LeaseId: $"lease-{_nextId()}",
            LeaseEpoch: epoch,
            RequiredCapabilities: requiredCapabilities,
            IssuedAt: now,
            ExpiresAt: now + leaseBudget,
            PolicyRevisionId: policyRevisionId);

        var commandId = $"cmd-{_nextId()}";
        var command = new CommandEnvelope
        {
            MessageId = commandId,
            SchemaVersion = "command.v1",
            MessageType = MessageTypes.ExecutionAssign,
            CorrelationId = run.Current.RunId,
            CausationId = null,
            IdempotencyKey = $"{assignment.AssignmentId}:{attemptId}",
            WorkflowRunId = run.Current.RunId,
            StageRunId = stage.Current.StageRunId,
            ExecutionId = executionId,
            AttemptId = attemptId,
            AssignmentId = assignment.AssignmentId,
            WorkerId = workerId,
            AgentId = agentId,
            LeaseId = assignment.LeaseId,
            LeaseEpoch = epoch,
            IssuedAt = assignment.IssuedAt,
            ExpiresAt = assignment.ExpiresAt,
            Traceparent = NewTraceparent(commandId),
            Payload = System.Text.Json.JsonSerializer.Serialize(new AssignCommandPayload(assignment, handoffId)),
            PolicyRevisionId = policyRevisionId,
        };

        // Validating the outbox row is part of planning, not mutation: if the
        // envelope were unserialisable or oversize we still hold nothing applied.
        var outboxMessage = OutboxMessage.NewCommand(command, now);

        _leases.Grant(assignment);
        _registry.AddAttempt(executionId, attemptId, epoch);
        _outbox.Add(outboxMessage);
        _sent.Record(assignment.AssignmentId, command);
        return assignment;
    }

    /// <summary>
    /// W3C trace context minted per command. The value is deterministic from
    /// the message id so tests and recovery can correlate without a live
    /// collector (doc 150 PR-011 requires traceparent, not a specific SDK).
    /// </summary>
    internal static string NewTraceparent(string messageId)
    {
        var span = Guid.NewGuid().ToString("N")[..16];
        var trace = System.Security.Cryptography.SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(messageId));
        var hex = Convert.ToHexString(trace[..16]).ToLowerInvariant();
        return $"00-{hex}-{span}-01";
    }

    /// <summary>Queues a cancel command for the current lease of an execution.</summary>
    public void DispatchCancel(string executionId, string reason)
    {
        var current = _leases.CurrentFor(executionId)
            ?? throw new NotFoundException($"execution '{executionId}' has no current assignment");

        var now = _clock();
        var cancelId = $"cmd-{_nextId()}";
        var command = new CommandEnvelope
        {
            MessageId = cancelId,
            SchemaVersion = "command.v1",
            MessageType = MessageTypes.ExecutionCancel,
            CorrelationId = current.WorkflowRunId,
            IdempotencyKey = $"{current.AssignmentId}:cancel",
            WorkflowRunId = current.WorkflowRunId,
            StageRunId = current.StageRunId,
            ExecutionId = current.ExecutionId,
            AttemptId = current.AttemptId,
            AssignmentId = current.AssignmentId,
            WorkerId = current.WorkerId,
            AgentId = current.AgentId,
            LeaseId = current.LeaseId,
            LeaseEpoch = current.LeaseEpoch,
            IssuedAt = now,
            ExpiresAt = current.ExpiresAt,
            Traceparent = NewTraceparent(cancelId),
            Payload = System.Text.Json.JsonSerializer.Serialize(new { reason }),
            PolicyRevisionId = current.PolicyRevisionId,
        };

        _outbox.AddCommand(command);
    }
}

/// <summary>
/// The result intake side of A1: inbox dedup, lease fencing, attempt/stage
/// transitions, single-outcome acceptance, and retry-or-DLQ decisions
/// (doc 151 §5.6 steps 1–6).
/// </summary>
public sealed class ServerResultProcessor
{
    private readonly WorkflowRegistry _registry;
    private readonly LeaseRegistry _leases;
    private readonly Inbox _inbox;
    private readonly RetryPlanner _planner;
    private readonly DeadLetterQueue _deadLetters;
    private readonly CommandDispatcher _dispatch;
    private readonly Func<DateTimeOffset> _clock;
    private readonly Func<string> _nextId;
    private readonly SentCommandLog _sent;
    private readonly PendingRetryQueue _retries;
    private readonly AttemptEvidenceLog? _evidence;

    public ServerResultProcessor(
        WorkflowRegistry registry,
        LeaseRegistry leases,
        Inbox inbox,
        RetryPlanner planner,
        DeadLetterQueue deadLetters,
        CommandDispatcher dispatch,
        Func<DateTimeOffset> clock,
        Func<string> nextId,
        SentCommandLog sent,
        PendingRetryQueue retries,
        AttemptEvidenceLog? evidence = null)
    {
        _registry = registry;
        _leases = leases;
        _inbox = inbox;
        _planner = planner;
        _deadLetters = deadLetters;
        _dispatch = dispatch;
        _clock = clock;
        _nextId = nextId;
        _sent = sent;
        _retries = retries;
        _evidence = evidence;
    }

    public ResultVerdict Process(ResultEnvelope result)
    {
        var audit = _registry.Audit;

        // Step 0: schema validation — malformed envelopes never reach the
        // state machine, and unknown major versions fail closed (doc 150 PR-016).
        var errors = EnvelopeValidator.Validate(result);
        if (errors.Count > 0)
        {
            var reason = $"schema rejected: {string.Join("; ", errors.Select(e => $"{e.Field} {e.Reason}"))}";
            audit.Append("server", "result.rejected", result.MessageId, reason);
            return new ResultVerdict(ResultOutcomeKind.RejectedSchema, reason);
        }

        // Execution summaries are best-effort observation traffic (doc 151
        // section 8.3: WSS/summary views must never carry final
        // success/failure semantics). They must not move the machines.
        if (result.MessageType == MessageTypes.ExecutionSummary)
        {
            audit.Append("server", "summary.received", result.AttemptId,
                "non-authoritative summary observed; no state change", result.CorrelationId);
            return new ResultVerdict(
                ResultOutcomeKind.RejectedNonAuthoritative,
                "execution.summary must not finalize attempts; publish the authoritative execution.result");
        }

        // Step 1: inbox dedup on message instance, then on business operation.
        var messageKey = Inbox.MessageKey(result.MessageId);
        if (!_inbox.TryReserve(messageKey, DedupKind.Message, out var existingMessage))
        {
            return new ResultVerdict(
                ResultOutcomeKind.Duplicate,
                $"message '{result.MessageId}' already processed: {existingMessage.ProcessedOutcome ?? "(in flight)"}");
        }

        var businessKey = Inbox.BusinessKey(result.IdempotencyKey);
        if (!_inbox.TryReserve(businessKey, DedupKind.Idempotency, out var existingBusiness))
        {
            _inbox.Complete(messageKey, "duplicate business operation");
            return new ResultVerdict(
                ResultOutcomeKind.Duplicate,
                $"idempotency key '{result.IdempotencyKey}' already processed: {existingBusiness.ProcessedOutcome ?? "(in flight)"}");
        }

        ResultVerdict verdict = ProcessFenced(result, audit);
        _inbox.Complete(messageKey, verdict.Kind + ": " + verdict.Reason);
        _inbox.Complete(businessKey, verdict.Kind.ToString());
        return verdict;
    }

    private ResultVerdict ProcessFenced(ResultEnvelope result, AuditTrail audit)
    {
        // Step 2: lease and identity checks before any business effect.
        var leaseVerdict = _leases.Check(result.AssignmentId, result.LeaseEpoch);
        switch (leaseVerdict)
        {
            case LeaseVerdict.Unknown:
                audit.Append("server", "result.rejected", result.AssignmentId, "unknown assignment", result.CorrelationId);
                return new ResultVerdict(ResultOutcomeKind.RejectedUnknownAssignment, "assignment was never granted");

            case LeaseVerdict.StaleEpoch:
                // doc 150 PR-008: a result under a superseded epoch can never
                // become the outcome, but the rejection itself is auditable.
                audit.Append("server", "result.stale_rejected", result.AssignmentId,
                    $"epoch {result.LeaseEpoch} superseded", result.CorrelationId);
                return new ResultVerdict(ResultOutcomeKind.RejectedStaleEpoch,
                    $"lease epoch {result.LeaseEpoch} is not current for this execution");

            case LeaseVerdict.Expired:
                audit.Append("server", "result.expired_rejected", result.AssignmentId,
                    "lease window elapsed", result.CorrelationId);
                TryExpireAttempt(result);
                return new ResultVerdict(ResultOutcomeKind.RejectedLeaseExpired,
                    "lease expired; recovery requires a new assignment at a new epoch");
        }

        var assignment = _leases.Require(result.AssignmentId);

        var mismatch = AssignmentValidator.ValidateResultAgainstAssignment(result, assignment);
        if (mismatch.Count > 0)
        {
            var reason = string.Join("; ", mismatch.Select(e => $"{e.Field} {e.Reason}"));
            audit.Append("server", "result.rejected", result.MessageId, reason, result.CorrelationId);
            return new ResultVerdict(ResultOutcomeKind.RejectedSchema, reason);
        }

        // The Server holds the exact command envelope it issued; the result
        // must be causally tied to it rather than merely self-consistent
        // (doc 151 section 5.6 ValidateResultFollowsCommand, wired into intake).
        // Unknown provenance FAILS CLOSED: accepting a result whose originating
        // command cannot be proven would let restored or partially committed
        // state legitimize fabricated outcomes (doc 150 section 10 rule 8).
        if (!_sent.TryGet(result.AssignmentId, out var issuedCommand))
        {
            var noProvenance = $"no issued command on record proves assignment '{result.AssignmentId}'; result origin cannot be verified";
            audit.Append("server", "result.rejected", result.MessageId, noProvenance, result.CorrelationId);
            return new ResultVerdict(ResultOutcomeKind.RejectedSchema, noProvenance);
        }

        var followErrors = EnvelopeValidator.ValidateResultFollowsCommand(issuedCommand, result);
        if (followErrors.Count > 0)
        {
            var reason = string.Join("; ", followErrors.Select(e => $"{e.Field} {e.Reason}"));
            audit.Append("server", "result.rejected", result.MessageId, reason, result.CorrelationId);
            return new ResultVerdict(ResultOutcomeKind.RejectedSchema, reason);
        }

        TrackedAttempt attempt;
        try
        {
            attempt = _registry.RequireAttempt(result.AttemptId);
        }
        catch (NotFoundException)
        {
            return new ResultVerdict(ResultOutcomeKind.RejectedSchema,
                $"attempt '{result.AttemptId}' is not a registered attempt");
        }

        if (!string.Equals(attempt.Current.ExecutionId, result.ExecutionId, StringComparison.Ordinal))
        {
            return new ResultVerdict(ResultOutcomeKind.RejectedSchema,
                "attempt belongs to a different execution than the result claims");
        }

        // Step 3/4: the attempt's terminal transition and its recorded result.
        var ctx = new TransitionContext(
            actor: $"node:{result.WorkerId}",
            reason: $"result {result.ResultStatus}",
            schemaVersion: result.SchemaVersion,
            causationId: result.MessageId);

        var attemptTarget = result.ResultStatus switch
        {
            AttemptResultStatus.Succeeded => ExecutionAttemptState.Succeeded,
            AttemptResultStatus.ChangesRequested => ExecutionAttemptState.Succeeded,
            AttemptResultStatus.Failed => ExecutionAttemptState.Failed,
            AttemptResultStatus.Cancelled => ExecutionAttemptState.Cancelled,
            AttemptResultStatus.Expired => ExecutionAttemptState.Expired,
            _ => ExecutionAttemptState.Failed,
        };

        try
        {
            CollapseLifecycle(result.AttemptId, attemptTarget, ctx);
            _registry.MoveAttempt(result.AttemptId, attemptTarget, ctx);
            _registry.RecordAttemptResult(result.AttemptId, new AttemptResult(
                result.AttemptId, result.ResultStatus, result.FailureCategory, result.OutcomeSummary));

            // Keep the bounded evidence (artifacts/commit/tests/findings) so a
            // later IssueHandoff is built from what actually happened, not from
            // what a stage-2 caller remembers (doc 150 PR-010).
            _evidence?.Record(result);
        }
        catch (DomainException e) when (e is IllegalTransitionException or InvalidValueException or DuplicateException)
        {
            audit.Append("server", "result.rejected", result.AttemptId, e.Message, result.CorrelationId);
            return new ResultVerdict(ResultOutcomeKind.RejectedIllegalTransition, e.Message);
        }

        var executionId = attempt.Current.ExecutionId;
        var stage = _registry.RequireStage(_stageIdFor(executionId));

        // Step 5: business resolution per result status.
        switch (result.ResultStatus)
        {
            case AttemptResultStatus.Succeeded:
                var outcome = new Outcome(
                    OutcomeId: $"out-{_nextId()}",
                    ExecutionId: executionId,
                    AcceptedAttemptId: result.AttemptId,
                    AcceptedAt: _clock());
                _registry.AcceptOutcome(executionId, outcome);
                if (stage.Machine.Current == StageRunState.Running)
                {
                    _registry.MoveStage(stage.Current.StageRunId, StageRunState.Succeeded, ctx);
                }

                audit.Append("server", "result.accepted", result.AttemptId, "outcome accepted", result.CorrelationId);
                return new ResultVerdict(ResultOutcomeKind.Accepted, "outcome accepted", executionId);

            case AttemptResultStatus.ChangesRequested:
                // The review's execution resolves with an accepted outcome —
                // changes_requested is its business result — and the follow-up
                // work is a NEW development StageRun, never a fix stage.
                var reviewOutcome = new Outcome(
                    OutcomeId: $"out-{_nextId()}",
                    ExecutionId: executionId,
                    AcceptedAttemptId: result.AttemptId,
                    AcceptedAt: _clock());
                _registry.AcceptOutcome(executionId, reviewOutcome);

                var created = _registry.RequestChangesIteration(
                    stage.Current.StageRunId, $"stg-{_nextId()}", ctx);
                audit.Append("server", "result.accepted", result.AttemptId,
                    $"changes requested; development iteration {created.Iteration} created", result.CorrelationId);
                return new ResultVerdict(
                    ResultOutcomeKind.Accepted, "changes requested", executionId, CreatedIteration: created);

            default:
                return ResolveFailure(result, executionId, stage, ctx, audit);
        }
    }

    private ResultVerdict ResolveFailure(
        ResultEnvelope result,
        string executionId,
        TrackedStage stage,
        TransitionContext ctx,
        AuditTrail audit)
    {
        // doc 150 PR-012: every failure is retryable-or-not by category, with
        // a bounded budget and a queryable terminal state either way.
        var failureNumber = _registry.RequireExecution(executionId).Attempts.Count(a => a.Result is not null);
        var decision = _planner.Decide(result.FailureCategory, failureNumber);

        if (decision.IsRetry)
        {
            // The retry waits for its backoff deadline instead of hammering
            // the provider immediately (doc 150 PR-012 "backoff 上限"), and the
            // next assignment inherits the lease record's worker, agent and
            // policy — never values the reporting result merely claims.
            var current = _leases.CurrentFor(executionId)!;
            var due = _clock() + decision.Delay!.Value;
            _retries.Schedule(new PendingRetry(
                executionId,
                due,
                current.WorkerId,
                current.AgentId,
                current.RequiredCapabilities,
                current.PolicyRevisionId,
                current.ExpiresAt - current.IssuedAt));

            audit.Append("server", "result.retry_scheduled", executionId,
                $"{result.FailureCategory} failure {failureNumber}; retry due {due:O} ({decision.Reason})",
                result.CorrelationId);
            return new ResultVerdict(ResultOutcomeKind.Accepted,
                $"failure recorded; retry scheduled for {due:O} ({decision.Reason})", executionId);
        }

        if (stage.Machine.Current is StageRunState.Running or StageRunState.Assigned)
        {
            _registry.MoveStage(stage.Current.StageRunId, StageRunState.Failed, ctx);
        }

        var entry = _deadLetters.Enqueue(new DeadLetterEntry(
            Id: $"dlq-{_nextId()}",
            MessageId: result.MessageId,
            ExecutionId: executionId,
            Category: result.FailureCategory,
            Reason: decision.Reason,
            EnqueuedAt: _clock()));

        audit.Append("server", "result.dead_lettered", executionId,
            $"{result.FailureCategory}: {decision.Reason}", result.CorrelationId);
        return new ResultVerdict(ResultOutcomeKind.Accepted,
            $"failure recorded; dead-lettered ({decision.Reason})", executionId, DeadLetterId: entry.Id);
    }

    /// <summary>
    /// A single result from the Node attests that the provider process started
    /// and ran; the Server has not seen the intermediate events. Recording the
    /// witnessing transitions keeps the attempt machine's frozen A0 legality
    /// (created -&gt; starting -&gt; running) instead of inventing an illegal
    /// direct move (doc 151 §4.3).
    /// </summary>
    private void CollapseLifecycle(string attemptId, ExecutionAttemptState target, TransitionContext ctx)
    {
        var attempt = _registry.RequireAttempt(attemptId);

        if (attempt.Machine.Current == ExecutionAttemptState.Created
            && target is not ExecutionAttemptState.Cancelled)
        {
            _registry.MoveAttempt(attemptId, ExecutionAttemptState.Starting,
                new TransitionContext(ctx.Actor, "process started (attested by result)", ctx.SchemaVersion, ctx.CausationId));
        }

        attempt = _registry.RequireAttempt(attemptId);
        if (attempt.Machine.Current == ExecutionAttemptState.Starting && target == ExecutionAttemptState.Succeeded)
        {
            _registry.MoveAttempt(attemptId, ExecutionAttemptState.Running,
                new TransitionContext(ctx.Actor, "process ran (attested by result)", ctx.SchemaVersion, ctx.CausationId));
        }
    }

    private void TryExpireAttempt(ResultEnvelope result)
    {
        TrackedAttempt attempt;
        try
        {
            attempt = _registry.RequireAttempt(result.AttemptId);
        }
        catch (NotFoundException)
        {
            return;
        }

        if (attempt.Machine.IsTerminal)
        {
            return;
        }

        try
        {
            if (attempt.Machine.Current == ExecutionAttemptState.Created)
            {
                _registry.MoveAttempt(result.AttemptId, ExecutionAttemptState.Starting,
                    new TransitionContext("server", "lease expiry attests the process started", SchemaVersions.Registry));
            }

            _registry.MoveAttempt(result.AttemptId, ExecutionAttemptState.Expired,
                new TransitionContext("server", "lease expired before result acceptance", SchemaVersions.Registry));
        }
        catch (IllegalTransitionException)
        {
            // Terminal-by-other-means attempts are left as they are; a fresh
            // attempt is simply replaced at a new epoch.
        }
    }

    private string _stageIdFor(string executionId) => _registry.RequireExecution(executionId).Current.StageRunId;
}
