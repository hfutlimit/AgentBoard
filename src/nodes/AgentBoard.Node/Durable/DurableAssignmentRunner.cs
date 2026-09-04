// SPDX-License-Identifier: MIT
using System.Security.Cryptography;
using System.Text;
using AgentBoard.Contracts;
using AgentBoard.Node.Agents;

namespace AgentBoard.Node.Durable;

/// <summary>
/// Connects the durable command journal to the existing provider adapters.
/// Broker consumers ACK after <see cref="Accept"/>; this runner then executes
/// every pending journal row and records the result in the local outbox before
/// marking the command complete. A restart can therefore replay pending work.
/// </summary>
public sealed class DurableAssignmentRunner
{
    private readonly NodeCommandReceiver _receiver;
    private readonly INodeCommandJournal _journal;
    private readonly AssignmentTracker _tracker;
    private readonly LocalEventStore _events;
    private readonly LocalResultOutbox _outbox;
    private readonly IAgentAdapterRegistry _adapters;
    private readonly CompiledPolicy _policy;
    private readonly ILocalWorkspaceResolver _workspaces;
    private readonly Func<DateTimeOffset> _clock;
    private readonly Dictionary<string, CancellationTokenSource> _active = new(StringComparer.Ordinal);
    private readonly Dictionary<string, CommandEnvelope> _cancelByAssignment = new(StringComparer.Ordinal);
    private readonly object _gate = new();

    public DurableAssignmentRunner(
        string workerId,
        INodeCommandJournal journal,
        AssignmentTracker tracker,
        LocalEventStore events,
        LocalResultOutbox outbox,
        IAgentAdapterRegistry adapters,
        CompiledPolicy policy,
        ILocalWorkspaceResolver workspaces,
        Func<DateTimeOffset>? clock = null,
        IApprovalAuthority? approvalAuthority = null)
    {
        _journal = journal;
        _tracker = tracker;
        _events = events;
        _outbox = outbox;
        _adapters = adapters;
        _policy = policy;
        _workspaces = workspaces ?? throw new ArgumentNullException(nameof(workspaces));
        _clock = clock ?? (() => DateTimeOffset.UtcNow);
        _receiver = new NodeCommandReceiver(workerId, journal, tracker, _clock);
        ApprovalAuthority = approvalAuthority;
    }

    public IApprovalAuthority? ApprovalAuthority { get; }

    public CommandAcceptance Accept(CommandEnvelope command) => _receiver.TryAccept(command);

    public IReadOnlyList<Assignment> RebuildAssignments() => _receiver.RebuildAfterRestart();

    public IReadOnlyList<CommandEnvelope> PendingCommands() => _journal.Pending();

    public async Task ExecuteAcceptedAsync(CommandEnvelope command, CancellationToken cancellationToken)
    {
        if (_outbox.ForCommand(command.MessageId) is not null)
        {
            _journal.MarkCompleted(command.MessageId);
            return;
        }

        if (command.MessageType == MessageTypes.ExecutionCancel)
        {
            CancellationTokenSource? active;
            lock (_gate)
            {
                _cancelByAssignment[command.AssignmentId] = command;
                _active.TryGetValue(command.AssignmentId, out active);
            }

            try { active?.Cancel(); }
            catch (ObjectDisposedException) { }
            _journal.MarkCompleted(command.MessageId);
            return;
        }

        if (command.MessageType != MessageTypes.ExecutionAssign)
        {
            throw new InvalidOperationException($"unsupported durable command '{command.MessageType}'");
        }

        var payload = AssignmentTracker.ParseAssignPayload(command);
        var assignment = payload.Assignment;
        using var linked = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        var leaseRemaining = assignment.ExpiresAt - _clock();
        if (leaseRemaining <= TimeSpan.Zero)
        {
            _outbox.Enqueue(Expired(command));
            _tracker.Release(assignment);
            _journal.MarkCompleted(command.MessageId);
            return;
        }
        linked.CancelAfter(leaseRemaining);
        lock (_gate)
        {
            _active[assignment.AssignmentId] = linked;
            if (_cancelByAssignment.ContainsKey(assignment.AssignmentId))
            {
                linked.Cancel();
            }
        }

        try
        {
            AppendEvent(assignment, command, "agentboard.execution.accepted", "assignment durably accepted");
            ResultEnvelope result;
            try
            {
                var workspace = payload.Workspace ?? payload.Handoff?.Workspace
                    ?? throw new InvalidDataException("assignment payload is missing Workspace");
                var stage = payload.StageType ?? payload.Handoff?.TargetStageType
                    ?? throw new InvalidDataException("assignment payload is missing StageType");
                var workingDirectory = _workspaces.Resolve(workspace);
                var providerId = payload.ProviderId ?? assignment.AgentId;
                var request = new PolicyDecisionRequest(
                    new PolicyAction(PolicyActionKinds.SpawnProviderProcess, $"provider:{providerId}"),
                    assignment.AgentId,
                    assignment.RequiredCapabilities,
                    stage,
                    assignment.WorkflowRunId,
                    workspace,
                    assignment.PolicyRevisionId,
                    ApprovalGranted: false);

                var pep = new PolicyEnforcementPoint(
                    new PolicyDecisionPoint(_policy, ApprovalAuthority, _clock),
                    line => AppendEvent(assignment, command, "agentboard.execution.policy_decision",
                        $"{line.Kind}:{line.Decision}:{line.Failure}"));

                var execution = await pep.ExecuteAsync(
                    request,
                    async ct =>
                    {
                        var adapter = _adapters.Get(providerId);
                        AppendEvent(assignment, command, "agentboard.execution.provider_started", adapter.AgentType);
                        var workloadType = stage == StageType.Review
                            ? WorkloadTypes.Review
                            : WorkloadTypes.Task;
                        return await adapter.ExecuteAsync(new ExecutionContext(
                            StableLong(assignment.ExecutionId),
                            assignment.ExecutionId,
                            workloadType,
                            payload.WorkItemId ?? StableLong(assignment.StageRunId),
                            checked((int)Math.Clamp(assignment.LeaseEpoch, 1, int.MaxValue)),
                            adapter.AgentType,
                            payload.Handoff?.TaskContext ?? payload.TaskContext,
                            payload.Handoff?.TaskContext ?? payload.TaskContext,
                            payload.TaskType,
                            workingDirectory,
                            DurableExecution: true,
                            StageType: stage,
                            Handoff: payload.Handoff), ct);
                    },
                    linked.Token);

                var cancel = CancelCommandFor(assignment.AssignmentId);
                if (cancellationToken.IsCancellationRequested && cancel is null)
                {
                    throw new OperationCanceledException(cancellationToken);
                }

                if (linked.IsCancellationRequested)
                {
                    result = cancel is not null ? Cancelled(cancel) : Expired(command);
                    if (cancel is null) _tracker.Release(assignment);
                }
                else
                {
                    result = execution.Outcome == EnforcementOutcome.Executed && execution.Value is not null
                        ? FromProvider(command, execution.Value)
                        : Failure(command, execution.Failure == FailureCategory.None
                            ? FailureCategory.ApprovalUnavailable
                            : execution.Failure,
                            execution.Outcome.ToString());
                }

                // A provider can finish after the assignment was superseded
                // or its lease elapsed. Never publish that stale business
                // result as success; report only the fenced terminal fact.
                if (!_tracker.MaySubmitResult(assignment.AssignmentId, _clock()))
                {
                    result = Expired(command);
                    _tracker.Release(assignment);
                }
            }
            catch (OperationCanceledException) when (
                cancellationToken.IsCancellationRequested
                && CancelCommandFor(assignment.AssignmentId) is null)
            {
                // Host shutdown is not a business cancellation. Leave the
                // accepted journal row pending so restart can recover it.
                throw;
            }
            catch (OperationCanceledException)
            {
                if (CancelCommandFor(assignment.AssignmentId) is { } cancel)
                {
                    result = Cancelled(cancel);
                }
                else
                {
                    result = Expired(command);
                    _tracker.Release(assignment);
                }
            }
            catch (InvalidDataException error)
            {
                result = Failure(command, FailureCategory.SchemaRejection,
                    $"assignment contract rejected ({error.GetType().Name})");
            }
            catch (LocalWorkspaceResolutionException error)
            {
                result = Failure(command, FailureCategory.SchemaRejection,
                    $"workspace mapping unavailable ({error.GetType().Name})");
            }
            catch (Exception error)
            {
                // Persist only the type: adapter exceptions may contain command
                // lines, paths or provider credentials.
                result = Failure(command, FailureCategory.ProviderFailure,
                    $"provider failed ({error.GetType().Name})");
            }

            // Do not catch either write: if the outbox or journal transition
            // fails, the accepted command remains pending and restart retries
            // it. Calling that a provider failure would erase the crash window.
            _outbox.Enqueue(result);
            AppendEvent(assignment, command, "agentboard.execution.result_durable", result.ResultStatus.ToString());
            _journal.MarkCompleted(command.MessageId);
        }
        finally
        {
            lock (_gate)
            {
                _active.Remove(assignment.AssignmentId);
                _cancelByAssignment.Remove(assignment.AssignmentId);
            }
        }
    }

    public async Task RecoverPendingAsync(CancellationToken cancellationToken)
    {
        RebuildAssignments();
        foreach (var command in _journal.Pending())
        {
            await ExecuteAcceptedAsync(command, cancellationToken);
        }
    }

    private ResultEnvelope FromProvider(CommandEnvelope command, AgentExecutionResult provider)
    {
        if (provider.Cancelled)
        {
            return Cancelled(CancelCommandFor(command.AssignmentId) ?? command);
        }

        var redaction = new SecretRedaction();
        var status = provider.Success ? AttemptResultStatus.Succeeded : AttemptResultStatus.Failed;
        var summary = provider.Success
            ? provider.OutputJson ?? "provider completed"
            : $"provider failed with exit {provider.ExitCode?.ToString() ?? "unknown"}";
        string? commitOrVersion = null;
        IReadOnlyList<string> testEvidence = Array.Empty<string>();
        IReadOnlyList<string> reviewFindings = Array.Empty<string>();
        IReadOnlyList<ArtifactReference> artifacts = Array.Empty<ArtifactReference>();

        if (provider.Success && !string.IsNullOrWhiteSpace(provider.OutputJson))
        {
            try
            {
                using var document = System.Text.Json.JsonDocument.Parse(provider.OutputJson);
                var root = document.RootElement;
                if (root.TryGetProperty("result_status", out var statusElement)
                    && TryParseStatus(statusElement.GetString(), out var declared))
                {
                    status = declared;
                }
                if (root.TryGetProperty("summary", out var summaryElement))
                    summary = summaryElement.GetString() ?? summary;
                if (root.TryGetProperty("commit_or_version", out var commitElement))
                    commitOrVersion = commitElement.GetString();
                if (root.TryGetProperty("test_evidence", out var testsElement))
                    testEvidence = ReadStrings(testsElement);
                if (root.TryGetProperty("review_findings", out var findingsElement))
                    reviewFindings = ReadStrings(findingsElement);
                if (root.TryGetProperty("artifact_references", out var artifactsElement))
                    artifacts = System.Text.Json.JsonSerializer.Deserialize<ArtifactReference[]>(
                        artifactsElement.GetRawText(), ContractJson.Options) ?? Array.Empty<ArtifactReference>();
            }
            catch (System.Text.Json.JsonException)
            {
                // Plain provider output is a valid summary; structured evidence
                // is an optional adapter capability, not a reason to discard a
                // successful attempt.
            }
        }

        var payload = AssignmentTracker.ParseAssignPayload(command);
        var stage = payload.StageType ?? payload.Handoff?.TargetStageType;
        if (status == AttemptResultStatus.ChangesRequested &&
            (stage is not (StageType.Review or StageType.Qa) ||
             stage == StageType.Qa &&
             (!testEvidence.Any(item => !string.IsNullOrWhiteSpace(item)) ||
              !reviewFindings.Any(item => !string.IsNullOrWhiteSpace(item)))))
            return Failure(command, FailureCategory.SchemaRejection,
                "business feedback requires a review/QA stage and QA findings with test evidence");

        var failure = status is AttemptResultStatus.Succeeded
            or AttemptResultStatus.ChangesRequested
            or AttemptResultStatus.Cancelled
            ? FailureCategory.None
            : FailureCategory.ProviderFailure;
        var candidate = Result(command, status, failure,
            Truncate(redaction.Redact(summary), PayloadLimits.MaxOutcomeSummaryBytes),
            artifacts, commitOrVersion, testEvidence, reviewFindings);
        return EnvelopeValidator.IsValid(candidate)
            ? candidate
            : Result(command, AttemptResultStatus.Failed, FailureCategory.SchemaRejection,
                "provider output failed result schema validation");
    }

    private ResultEnvelope Failure(CommandEnvelope command, FailureCategory category, string summary) =>
        Result(command, AttemptResultStatus.Failed, category, summary);

    private ResultEnvelope Cancelled(CommandEnvelope command) =>
        Result(command, AttemptResultStatus.Cancelled, FailureCategory.None, "cancelled");

    private ResultEnvelope Expired(CommandEnvelope command) =>
        Result(command, AttemptResultStatus.Expired, FailureCategory.LeaseExpired,
            "assignment lease expired before result submission");

    private ResultEnvelope Result(
        CommandEnvelope command,
        AttemptResultStatus status,
        FailureCategory failure,
        string summary,
        IReadOnlyList<ArtifactReference>? artifacts = null,
        string? commitOrVersion = null,
        IReadOnlyList<string>? testEvidence = null,
        IReadOnlyList<string>? reviewFindings = null) => new()
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
        ResultStatus = status,
        FailureCategory = failure,
        OutcomeSummary = summary,
        ArtifactReferences = artifacts ?? Array.Empty<ArtifactReference>(),
        CommitOrVersion = commitOrVersion,
        TestEvidence = testEvidence ?? Array.Empty<string>(),
        ReviewFindings = reviewFindings ?? Array.Empty<string>(),
        Traceparent = command.Traceparent,
        CreatedAt = _clock(),
    };

    private CommandEnvelope? CancelCommandFor(string assignmentId)
    {
        lock (_gate)
        {
            return _cancelByAssignment.GetValueOrDefault(assignmentId);
        }
    }

    private void AppendEvent(Assignment assignment, CommandEnvelope command, string kind, string data) =>
        _events.TryAppend(LocalEvents.For(
            assignment.WorkerId, assignment.AttemptId, kind,
            assignment.WorkflowRunId, data, _clock(), command.MessageId), out _, out _);

    private static IReadOnlyList<string> ReadStrings(System.Text.Json.JsonElement element) =>
        element.ValueKind == System.Text.Json.JsonValueKind.Array
            ? element.EnumerateArray()
                .Where(item => item.ValueKind == System.Text.Json.JsonValueKind.String)
                .Select(item => item.GetString()!)
                .ToArray()
            : Array.Empty<string>();

    private static bool TryParseStatus(string? value, out AttemptResultStatus status)
    {
        var normalized = value?.Replace("_", "", StringComparison.Ordinal)
            .Replace("-", "", StringComparison.Ordinal);
        foreach (var candidate in Enum.GetValues<AttemptResultStatus>())
        {
            if (string.Equals(candidate.ToString(), normalized, StringComparison.OrdinalIgnoreCase))
            {
                status = candidate;
                return true;
            }
        }

        status = default;
        return false;
    }

    private static long StableLong(string value)
    {
        var bytes = SHA256.HashData(Encoding.UTF8.GetBytes(value));
        return BitConverter.ToInt64(bytes, 0) & long.MaxValue;
    }

    private static string Truncate(string value, int maxBytes)
    {
        if (Encoding.UTF8.GetByteCount(value) <= maxBytes)
        {
            return value;
        }

        var chars = value.Length;
        while (chars > 0 && Encoding.UTF8.GetByteCount(value.AsSpan(0, chars)) > maxBytes)
        {
            chars--;
        }

        return value[..chars];
    }
}
