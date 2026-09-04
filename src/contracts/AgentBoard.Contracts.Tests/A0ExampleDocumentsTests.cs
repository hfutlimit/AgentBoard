using System.Text.Json;
using System.Text.Json.Serialization;
using AgentBoard.Contracts;
using Xunit;

namespace AgentBoard.Contracts.Tests;

/// <summary>
/// The representative wire examples required by doc 154 (A0 deliverable), held
/// to the same bar as the rest of the contract: each file is deserialised with
/// the production naming policy and then run through the real validator.
/// </summary>
/// <remarks>
/// Examples kept only in documentation rot silently — nobody re-reads them, and
/// a stale example is worse than none because it teaches the wrong shape. These
/// are executable, so drift fails the build.
/// </remarks>
public sealed class A0ExampleDocumentsTests
{
    /// <summary>
    /// The wire shape: snake_case, matching the doc 151 envelopes.
    /// Case-insensitive matching is deliberately left off so a renamed
    /// property surfaces as a validation failure rather than being silently
    /// absorbed.
    /// </summary>
    private static readonly JsonSerializerOptions Wire = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        Converters = { new JsonStringEnumConverter(JsonNamingPolicy.SnakeCaseLower) },
    };

    [Fact]
    public void Command_example_deserializes_and_validates()
    {
        var command = Load<CommandEnvelope>("command.json");

        Assert.Empty(EnvelopeValidator.Validate(command));
        Assert.Equal(3, command.LeaseEpoch);
        Assert.Equal("policy-rev-17", command.PolicyRevisionId);
    }

    [Fact]
    public void Result_example_deserializes_and_validates()
    {
        var result = Load<ResultEnvelope>("result.json");

        Assert.Empty(EnvelopeValidator.Validate(result));
        Assert.Equal(AttemptResultStatus.Succeeded, result.ResultStatus);
        Assert.Single(result.ArtifactReferences);
        Assert.True(result.ArtifactReferences[0].HasWellFormedDigest());
    }

    [Fact]
    public void Event_example_deserializes_and_validates()
    {
        var envelope = Load<EventEnvelope>("event.json");

        Assert.Empty(EnvelopeValidator.Validate(envelope));
        Assert.StartsWith("node://", envelope.Source, StringComparison.Ordinal);
        Assert.Equal($"{envelope.Source}|{envelope.EventId}", envelope.DedupKey);
    }

    [Fact]
    public void Handoff_example_deserializes_and_validates()
    {
        var handoff = Load<HandoffContext>("handoff.json");

        Assert.Empty(EnvelopeValidator.Validate(handoff));
        Assert.Equal(StageType.Review, handoff.TargetStageType);
        Assert.NotNull(handoff.Workspace);
    }

    [Fact]
    public void Agent_profile_example_deserializes_and_validates()
    {
        var profile = Load<AgentProfile>("agent-profile.json");

        Assert.Empty(ProfileValidator.Validate(profile));
        Assert.Equal(new[] { StageType.Development }, profile.AssignableStageTypes());
    }

    [Fact]
    public void Provider_definition_example_deserializes_and_validates()
    {
        var provider = Load<ProviderDefinition>("provider-definition.json");

        Assert.Empty(ProfileValidator.Validate(provider));
        Assert.Empty(ProviderAdapterCapabilities.MissingMandatory(provider.AdapterCapabilities));
    }

    [Fact]
    public void Workflow_version_example_deserializes_and_validates()
    {
        var version = Load<WorkflowVersion>("workflow-version.json");

        Assert.Empty(WorkflowValidator.Validate(version));
        Assert.Equal(3, version.Nodes.Count);
    }

    [Fact]
    public void Assignment_example_deserializes_and_validates()
    {
        var assignment = Load<Assignment>("assignment.json");

        Assert.Empty(AssignmentValidator.Validate(assignment));
        Assert.False(assignment.IsExpired(assignment.IssuedAt));
    }

    // -------------------------------------------------------------------------
    // The examples must describe one coherent attempt, not three unrelated ones
    // -------------------------------------------------------------------------

    [Fact]
    public void Command_result_and_assignment_examples_describe_the_same_attempt()
    {
        var assignment = Load<Assignment>("assignment.json");
        var command = Load<CommandEnvelope>("command.json");
        var result = Load<ResultEnvelope>("result.json");

        // An example set that does not hang together is how readers end up
        // guessing which fields must match across messages.
        Assert.Empty(AssignmentValidator.ValidateCommandAgainstAssignment(command, assignment));
        Assert.Empty(AssignmentValidator.ValidateResultAgainstAssignment(result, assignment));
        Assert.Empty(EnvelopeValidator.ValidateResultFollowsCommand(command, result));
    }

    [Fact]
    public void Profile_and_provider_examples_are_mutually_consistent()
    {
        var profile = Load<AgentProfile>("agent-profile.json");
        var provider = Load<ProviderDefinition>("provider-definition.json");

        Assert.Empty(ProfileValidator.ValidateProfileAgainstProvider(profile, provider));
    }

    [Fact]
    public void Every_example_file_is_covered_by_a_test()
    {
        // Guards against an example being added and then forgotten.
        var files = Directory
            .GetFiles(Path.Combine(AppContext.BaseDirectory, "Examples"), "*.json")
            .Select(Path.GetFileName)
            .OrderBy(name => name, StringComparer.Ordinal)
            .ToArray();

        Assert.Equal(
            new[]
            {
                "agent-profile.json",
                "assignment.json",
                "command.json",
                "event.json",
                "handoff.json",
                "provider-definition.json",
                "result.json",
                "workflow-version.json",
            },
            files);
    }

    private static T Load<T>(string fileName)
    {
        var path = Path.Combine(AppContext.BaseDirectory, "Examples", fileName);

        Assert.True(File.Exists(path), $"example '{fileName}' was not copied to the output directory");

        var value = JsonSerializer.Deserialize<T>(File.ReadAllText(path), Wire);
        Assert.NotNull(value);
        return value;
    }
}
