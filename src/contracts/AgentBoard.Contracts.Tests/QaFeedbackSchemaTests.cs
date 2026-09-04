using AgentBoard.Contracts;
using Xunit;

namespace AgentBoard.Contracts.Tests;

public sealed class QaFeedbackSchemaTests
{
    [Fact]
    public void Qa_feedback_is_excluded_from_success_navigation_and_its_budget_is_hashed()
    {
        var graph = Version(3);
        Assert.Empty(WorkflowValidator.Validate(graph));
        Assert.Equal(StageType.Development, WorkflowGraphNavigator.EntryNode(graph).StageType);
        Assert.Null(WorkflowGraphNavigator.Successor(graph, StageType.Qa));
        Assert.Equal(StageType.Development, WorkflowGraphNavigator.FeedbackSuccessor(graph, StageType.Qa).StageType);
        Assert.NotEqual(graph.ContentHash, Version(2).ContentHash);
    }

    [Theory]
    [InlineData(null)]
    [InlineData(0)]
    [InlineData(11)]
    public void Qa_feedback_rejects_missing_or_unbounded_budget(int? budget) =>
        Assert.NotEmpty(WorkflowValidator.Validate(Version(budget)));

    [Fact]
    public void Qa_feedback_cannot_be_added_to_a_legacy_schema() =>
        Assert.NotEmpty(WorkflowValidator.Validate(Version(3) with { SchemaVersion = "workflow.v1" }));

    [Fact]
    public void Rework_cannot_bypass_review()
    {
        var graph = Version(3);
        var nodes = graph.Nodes.Where(n => n.StageType != StageType.Review)
            .Select(n => n.StageType == StageType.Development ? n with { AllowedTransitions = new[] { StageType.Qa } } : n)
            .ToArray();
        Assert.NotEmpty(WorkflowValidator.Validate(graph with { Nodes = nodes, ContentHash = WorkflowGraph.ComputeContentHash(nodes) }));
    }

    [Fact]
    public void Success_cycles_cannot_bypass_the_rework_budget()
    {
        var graph = Version(3);
        var nodes = graph.Nodes.Select(n => n.StageType == StageType.Qa
            ? n with { AllowedTransitions = new[] { StageType.Development, StageType.Design } } : n)
            .Append(Node(StageType.Design, StageType.Qa)).ToArray();
        Assert.Contains(WorkflowValidator.Validate(graph with { Nodes = nodes }),
            error => error.Reason.Contains("cycle on the success path"));
    }

    private static WorkflowVersion Version(int? maximum)
    {
        var nodes = new[]
        {
            Node(StageType.Development, StageType.Review),
            Node(StageType.Review, StageType.Development, StageType.Qa),
            Node(StageType.Qa, StageType.Development) with { MaxReworkIterations = maximum },
        };
        return new WorkflowVersion("qa-feedback", "definition", 1, "workflow.v1.1", nodes, WorkflowGraph.ComputeContentHash(nodes));
    }

    private static WorkflowNode Node(StageType stage, params StageType[] targets) => new(
        stage.ToString(), stage, stage.ToString(), "{}", "{}", targets, "retry", "policy", new StageBudget(600, 600), true);
}
