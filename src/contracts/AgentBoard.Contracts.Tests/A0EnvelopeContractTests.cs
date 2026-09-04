using AgentBoard.Contracts;
using Xunit;

namespace AgentBoard.Contracts.Tests;

/// <summary>
/// A0 contract tests for the Command / Result / Event envelopes and the
/// HandoffContext. Each test names the doc 150 / doc 151 clause it enforces.
/// </summary>
public sealed class A0EnvelopeContractTests
{
    private static readonly DateTimeOffset IssuedAt =
        new(2026, 9, 4, 0, 0, 0, TimeSpan.Zero);

    // -------------------------------------------------------------------------
    // Happy path
    // -------------------------------------------------------------------------

    [Fact]
    public void A_well_formed_command_is_valid()
    {
        Assert.True(EnvelopeValidator.IsValid(ValidCommand()));
    }

    [Fact]
    public void A_well_formed_result_is_valid()
    {
        Assert.True(EnvelopeValidator.IsValid(ValidResult()));
    }

    // -------------------------------------------------------------------------
    // doc 151 §5.4 fencing
    // -------------------------------------------------------------------------

    [Theory]
    [InlineData(0)]
    [InlineData(-1)]
    public void Command_rejects_a_non_positive_lease_epoch(long epoch)
    {
        var command = ValidCommand() with { LeaseEpoch = epoch };

        // An epoch that cannot increase cannot fence anything, so a stale Node
        // would be free to overwrite a newer assignment.
        Assert.False(EnvelopeValidator.IsValid(command));
        Assert.Contains(
            EnvelopeValidator.Validate(command),
            e => e.Field == nameof(CommandEnvelope.LeaseEpoch));
    }

    [Fact]
    public void Command_rejects_an_expiry_at_or_before_issue()
    {
        var command = ValidCommand() with { ExpiresAt = IssuedAt };

        Assert.False(EnvelopeValidator.IsValid(command));
    }

    // -------------------------------------------------------------------------
    // doc 150 NFR-008 bounded payload
    // -------------------------------------------------------------------------

    [Fact]
    public void Command_rejects_an_oversized_inline_payload()
    {
        var command = ValidCommand() with { Payload = new string('x', 64 * 1024 + 1) };

        Assert.False(EnvelopeValidator.IsValid(command));
        Assert.Contains(
            EnvelopeValidator.Validate(command),
            e => e.Field == nameof(CommandEnvelope.Payload));
    }

    [Fact]
    public void Command_rejects_payload_and_reference_set_together()
    {
        var command = ValidCommand() with
        {
            Payload = "inline",
            PayloadReference = "artifact://abc",
        };

        // Two sources of truth for the same payload is how a truncated or
        // duplicated body goes unnoticed.
        Assert.False(EnvelopeValidator.IsValid(command));
    }

    [Fact]
    public void Result_rejects_an_oversized_outcome_summary()
    {
        var result = ValidResult() with { OutcomeSummary = new string('x', 8 * 1024 + 1) };

        Assert.False(EnvelopeValidator.IsValid(result));
        Assert.Contains(
            EnvelopeValidator.Validate(result),
            e => e.Field == nameof(ResultEnvelope.OutcomeSummary));
    }

    // -------------------------------------------------------------------------
    // doc 151 §5.6 / doc 150 PR-011 failure classification
    // -------------------------------------------------------------------------

    [Fact]
    public void Failed_result_requires_a_failure_category()
    {
        var result = ValidResult() with
        {
            ResultStatus = AttemptResultStatus.Failed,
            FailureCategory = FailureCategory.None,
        };

        // A failure with no category forces the operator to parse prose, which
        // PR-011 forbids.
        Assert.False(EnvelopeValidator.IsValid(result));
        Assert.Contains(
            EnvelopeValidator.Validate(result),
            e => e.Field == nameof(ResultEnvelope.FailureCategory));
    }

    [Fact]
    public void Succeeded_result_must_not_carry_a_failure_category()
    {
        var result = ValidResult() with
        {
            ResultStatus = AttemptResultStatus.Succeeded,
            FailureCategory = FailureCategory.ProviderFailure,
        };

        Assert.False(EnvelopeValidator.IsValid(result));
    }

    // -------------------------------------------------------------------------
    // doc 151 §10 rule 8 — unknown things are refused
    // -------------------------------------------------------------------------

    [Fact]
    public void Unknown_message_type_is_rejected()
    {
        var command = ValidCommand() with { MessageType = "execution.???" };

        Assert.False(EnvelopeValidator.IsValid(command));
    }

    // -------------------------------------------------------------------------
    // doc 150 PR-011 / doc 151 §8.1 trace propagation
    // -------------------------------------------------------------------------

    [Fact]
    public void Command_requires_a_traceparent()
    {
        var command = ValidCommand() with { Traceparent = null };

        Assert.False(EnvelopeValidator.IsValid(command));
        Assert.Contains(
            EnvelopeValidator.Validate(command),
            e => e.Field == nameof(CommandEnvelope.Traceparent));
    }

    // -------------------------------------------------------------------------
    // Invariant 8: lease expiry rejects stale results
    // -------------------------------------------------------------------------

    [Fact]
    public void A_well_formed_result_links_to_its_command()
    {
        var command = ValidCommand();

        Assert.Empty(EnvelopeValidator.ValidateResultFollowsCommand(command, ValidResult(command)));
    }

    [Fact]
    public void A_result_from_an_older_lease_epoch_is_rejected_as_stale()
    {
        var command = ValidCommand() with { LeaseEpoch = 4 };
        var stale = ValidResult(command) with { LeaseEpoch = 3 };

        var errors = EnvelopeValidator.ValidateResultFollowsCommand(command, stale);

        Assert.Contains(errors, e => e.Field == nameof(ResultEnvelope.LeaseEpoch));
    }

    [Fact]
    public void A_result_without_the_command_as_cause_is_rejected()
    {
        var command = ValidCommand();
        var orphan = ValidResult(command) with { CausationId = "some-other-message" };

        var errors = EnvelopeValidator.ValidateResultFollowsCommand(command, orphan);

        Assert.Contains(errors, e => e.Field == nameof(ResultEnvelope.CausationId));
    }

    [Fact]
    public void A_result_for_a_different_assignment_is_rejected()
    {
        var command = ValidCommand();
        var mismatched = ValidResult(command) with { AssignmentId = "assignment-999" };

        var errors = EnvelopeValidator.ValidateResultFollowsCommand(command, mismatched);

        Assert.Contains(errors, e => e.Field == nameof(ResultEnvelope.AssignmentId));
    }

    // -------------------------------------------------------------------------
    // doc 151 §5.7 events
    // -------------------------------------------------------------------------

    [Fact]
    public void Event_requires_a_node_source_to_be_globally_dedupable()
    {
        var envelope = ValidEvent() with { Source = "worker-1" };

        Assert.False(EnvelopeValidator.IsValid(envelope));
    }

    [Fact]
    public void Event_dedup_key_combines_source_and_event_id()
    {
        var envelope = ValidEvent();

        Assert.Equal($"{envelope.Source}|{envelope.EventId}", envelope.DedupKey);
    }

    [Fact]
    public void Two_events_with_the_same_source_and_id_collapse_to_one_key()
    {
        var first = ValidEvent();
        var duplicate = first with { Time = first.Time.AddSeconds(1) };

        // Redelivery must be detectable by key alone, without comparing bodies.
        Assert.Equal(first.DedupKey, duplicate.DedupKey);
    }

    [Fact]
    public void Events_from_different_nodes_never_collide()
    {
        var first = ValidEvent();
        var other = first with
        {
            Source = "node://worker-2/attempt/attempt-1",
        };

        Assert.NotEqual(first.DedupKey, other.DedupKey);
    }

    // -------------------------------------------------------------------------
    // doc 151 §7 handoff
    // -------------------------------------------------------------------------

    [Fact]
    public void A_well_formed_handoff_is_valid()
    {
        Assert.True(EnvelopeValidator.IsValid(ValidHandoff()));
    }

    [Fact]
    public void Handoff_requires_a_workspace()
    {
        var handoff = ValidHandoff() with { Workspace = null };

        // Without a workspace identity there is nothing to pin the target
        // stage's working copy to.
        Assert.False(EnvelopeValidator.IsValid(handoff));
    }

    [Fact]
    public void Handoff_requires_at_least_one_capability()
    {
        var handoff = ValidHandoff() with { RequiredCapabilities = Array.Empty<string>() };

        Assert.False(EnvelopeValidator.IsValid(handoff));
    }

    [Fact]
    public void Handoff_rejects_an_artifact_without_a_valid_digest()
    {
        var handoff = ValidHandoff() with
        {
            ArtifactReferences = new[]
            {
                new ArtifactReference("artifact://patch-1", "not-a-digest", 123),
            },
        };

        Assert.False(EnvelopeValidator.IsValid(handoff));
    }

    [Fact]
    public void Expired_artifacts_are_detectable()
    {
        var artifact = new ArtifactReference(
            "artifact://patch-1",
            new string('a', 64),
            123,
            ExpiresAt: new DateTimeOffset(2026, 9, 4, 0, 0, 0, TimeSpan.Zero));

        Assert.False(artifact.IsExpired(new DateTimeOffset(2026, 9, 3, 0, 0, 0, TimeSpan.Zero)));
        Assert.True(artifact.IsExpired(new DateTimeOffset(2026, 9, 5, 0, 0, 0, TimeSpan.Zero)));
    }

    // -------------------------------------------------------------------------
    // Boundary assertions: what these contracts must NOT be able to carry
    // -------------------------------------------------------------------------

    [Fact]
    public void Result_has_no_field_for_prompt_credential_or_raw_output()
    {
        // doc 151 §5.6 / doc 150 NFR-008. Adding one of these fields is the
        // most likely way the Node detail boundary gets breached by accident.
        var forbidden = new[] { "Prompt", "Credential", "Stdout", "FileContent", "Token", "Secret" };

        foreach (var property in typeof(ResultEnvelope).GetProperties())
        {
            foreach (var term in forbidden)
            {
                Assert.DoesNotContain(term, property.Name, StringComparison.OrdinalIgnoreCase);
            }
        }
    }

    [Fact]
    public void Handoff_does_not_carry_a_provider_session()
    {
        // doc 151 §7: the target stage must depend on context, artifacts and
        // evidence only, never on a live session from the source stage.
        var forbidden = new[] { "Session", "ProviderSession" };

        foreach (var property in typeof(HandoffContext).GetProperties())
        {
            foreach (var term in forbidden)
            {
                Assert.DoesNotContain(term, property.Name, StringComparison.OrdinalIgnoreCase);
            }
        }
    }

    // -------------------------------------------------------------------------
    // Builders
    // -------------------------------------------------------------------------

    private static CommandEnvelope ValidCommand() => new()
    {
        MessageId = "msg-1",
        SchemaVersion = "command.v1",
        MessageType = MessageTypes.ExecutionAssign,
        CorrelationId = "run-1",
        IdempotencyKey = "assignment-1:attempt-1",
        WorkflowRunId = "run-1",
        StageRunId = "stage-1",
        ExecutionId = "exec-1",
        AttemptId = "attempt-1",
        AssignmentId = "assignment-1",
        WorkerId = "worker-1",
        AgentId = "agent.dev.codex",
        Traceparent = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
        LeaseId = "lease-1",
        LeaseEpoch = 3,
        PolicyRevisionId = "policy-rev-17",
        IssuedAt = IssuedAt,
        ExpiresAt = IssuedAt.AddMinutes(5),
    };

    private static ResultEnvelope ValidResult(CommandEnvelope? command = null)
    {
        var cmd = command ?? ValidCommand();

        return new ResultEnvelope
        {
            MessageId = "msg-2",
            SchemaVersion = "result.v1",
            MessageType = MessageTypes.ExecutionResult,
            CorrelationId = cmd.CorrelationId,
            CausationId = cmd.MessageId,
            IdempotencyKey = cmd.IdempotencyKey,
            WorkflowRunId = cmd.WorkflowRunId,
            StageRunId = cmd.StageRunId,
            ExecutionId = cmd.ExecutionId,
            AttemptId = cmd.AttemptId,
            AssignmentId = cmd.AssignmentId,
            WorkerId = cmd.WorkerId,
            AgentId = cmd.AgentId,
            Traceparent = cmd.Traceparent,
            LeaseEpoch = cmd.LeaseEpoch,
            ResultStatus = AttemptResultStatus.Succeeded,
            CreatedAt = IssuedAt.AddMinutes(1),
        };
    }

    private static EventEnvelope ValidEvent() => new()
    {
        EventId = "event-1",
        Source = "node://worker-1/attempt/attempt-1",
        EventType = "agentboard.execution.tool_call",
        SchemaVersion = "execution-event.v1",
        Time = IssuedAt,
        Subject = "attempt-1",
        CorrelationId = "run-1",
        CausationId = "msg-1",
        Traceparent = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
        Data = "{}",
    };

    private static HandoffContext ValidHandoff() => new()
    {
        HandoffId = "handoff-1",
        SourceStageRunId = "development-iteration-2",
        SourceOutcomeId = "outcome-1",
        TargetStageType = StageType.Review,
        TaskContext = "{\"task\":\"review the patch\"}",
        ArtifactReferences = new[]
        {
            new ArtifactReference("artifact://patch-1", new string('a', 64), 123),
        },
        Workspace = new WorkspaceReference("project-1", "workspace-1", "commit-sha"),
        CommitOrVersion = "commit-sha",
        TestEvidence = Array.Empty<string>(),
        ReviewFindings = Array.Empty<string>(),
        ContextVersion = "handoff.v1",
        RequiredCapabilities = new[] { "review" },
    };
}
