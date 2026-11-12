using System.Net;
using System.Text.Json;
using System.Text.Json.Nodes;
using AgentBoard.Contracts;
using AgentBoard.Node.WorkerOwned;
using AgentBoard.Node.Agents;
using Xunit;

namespace AgentBoard.Node.Tests;

public class WorkerOwnedTests
{
    [Fact]
    public void Discussion_reply_is_read_only_and_targeted_without_an_eighth_work_kind()
    {
        var discussion = new { id = 9, status = "open", turn = 2, subject = "review_findings",
            owner_agent = "original-dev", reviewer_agent = "original-reviewer" };
        var item = JsonSerializer.SerializeToElement(new { id = 42, type = "dev", status = "in_review",
            ready = true, story_status = "in_progress", review_round = 0, discussion });
        // Workflow planning moved Server-side (serverplan protocol). The Worker
        // only has to render the read-only discussion-turn prompt and route the
        // reply onto the targeted agent queue.
        var prompt = WorkPlanner.Prompt("dev", JsonSerializer.Serialize(new { item, discussion }));
        Assert.Contains("Leave ALL files and HEAD unchanged", prompt);
        Assert.Contains("position='agree'|'disagree'|'clarify'", prompt);
        Assert.DoesNotContain("commit your changes", prompt);
        Assert.Equal("agentboard.work.v2.project.8.dev.agent.ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb",
            WorkerWorkKinds.AgentQueue(8, "dev", "a"));
        Assert.Equal(7, WorkerWorkKinds.All.Count);
    }

    [Fact]
    public void Review_prompt_requires_discussion_before_rejection()
    {
        Assert.Contains("Never reject or create bugs before discussion", WorkPlanner.Prompt("dev_review", "{}"));
    }

    [Fact]
    public void Expanded_qa_defect_feedback_preserves_evidence_in_description()
    {
        var output = JsonNode.Parse("""{"decision":"submit","tests_passed":false,"defects":[{"title":"Unicode","description":"fallback","expected_result":"Chinese","actual_result":"world"}]}""")!.AsObject();
        var error = Assert.Throws<InvalidDataException>(() => WorkPlanner.ValidateResult("qa", output));
        Assert.Contains("INSIDE the description", error.Message);
        output["defects"] = JsonNode.Parse("""[{"title":"Unicode","description":"GET /greet?name=张三; expected Chinese, actual world; evidence /tmp/result.json"}]""");
        WorkPlanner.ValidateResult("qa", output);
    }

    [Theory]
    [InlineData("null")]
    [InlineData("{}")]
    [InlineData("[]")]
    [InlineData("42")]
    [InlineData("\" \"")]
    public void Proposal_spec_type_feedback_is_actionable(string spec)
    {
        var output = JsonNode.Parse("{\"decision\":\"finalize\",\"spec\":" + spec + "}")!.AsObject();
        var error = Assert.Throws<InvalidDataException>(() => WorkPlanner.ValidateResult("proposal", output));
        Assert.Contains("JSON STRING", error.Message);
        output["spec"] = "# Scope\nAn actionable specification";
        WorkPlanner.ValidateResult("proposal", output);
        Assert.Contains("spec MUST be a nonempty JSON STRING", WorkPlanner.Prompt("proposal", "{}"));
    }

    [Fact]
    public void Journal_preserves_claim_and_completion_across_reopen()
    {
        var path = Path.Combine(Path.GetTempPath(), $"worker-owned-journal-test-{Guid.NewGuid():N}.db");
        try
        {
            var entry = new JournalEntry(7, "agent-a", Guid.NewGuid().ToString("N"), null);
            new WorkJournal(path, "server-a|worker-a").Save(entry);
            Assert.Throws<InvalidOperationException>(() => new WorkJournal(path, "server-b|worker-a"));
            Assert.Throws<InvalidOperationException>(() => new WorkJournal(path, "server-a|worker-b"));
            Assert.Equal(entry, new WorkJournal(path, "server-a|worker-a").Get(7));
            entry = entry with { Result = "{\"decision\":\"submit\",\"summary\":\"中文结果\"}" };
            new WorkJournal(path, "server-a|worker-a").Save(entry);
            Assert.Equal(entry, new WorkJournal(path, "server-a|worker-a").Get(7));
            new WorkJournal(path, "server-a|worker-a").Remove(7);
            Assert.Null(new WorkJournal(path, "server-a|worker-a").Get(7));
        }
        finally
        {
            Microsoft.Data.Sqlite.SqliteConnection.ClearAllPools();
            foreach (var suffix in new[] { "", "-wal", "-shm" })
                if (File.Exists(path + suffix)) File.Delete(path + suffix);
        }
    }

    [Theory]
    [InlineData("{\"type\":42}")]
    [InlineData("{\"type\":\"item.completed\",\"item\":\"warning\"}")]
    [InlineData("{\"type\":\"item.completed\",\"item\":{\"type\":\"agent_message\",\"text\":{}}}")]
    public void Unexpected_provider_envelope_shapes_do_not_crash_parser(string warning)
    {
        var output = warning + "\n{\"decision\":\"submit\",\"summary\":\"ok\"}";
        using var parsed = JsonDocument.Parse(SharedAdapterHelpers.TryExtractProviderJson(output)!);
        Assert.Equal("submit", parsed.RootElement.GetProperty("decision").GetString());
    }
    [Fact]
    public void Pretty_json_arrays_do_not_get_mistaken_for_provider_event_envelopes()
    {
        const string output = """
            {
              "decision": "ask",
              "summary": "中文需求",
              "questions": [
                "Which deployment target?"
              ]
            }
            """;
        using var result = JsonDocument.Parse(SharedAdapterHelpers.TryExtractProviderJson(output)!);
        Assert.Equal("ask", result.RootElement.GetProperty("decision").GetString());
        Assert.Equal("Which deployment target?", result.RootElement.GetProperty("questions")[0].GetString());
    }
    [Fact]
    public void Seven_kinds_are_distinct_from_legacy_events()
    {
        Assert.Equal(7, WorkerWorkKinds.All.Count);
        Assert.Equal("qa_review", WorkerWorkKinds.ForTask("qa", true));
        Assert.Equal("dev", WorkerWorkKinds.ForTask("bug"));
        foreach (var old in new[] { "ticket", "rework", "review", "#" })
            Assert.Throws<ArgumentException>(() => WorkerWorkKinds.Queue(8, old));
    }

    [Fact]
    public void All_agents_share_worker_projects_but_keep_separate_work_capabilities()
    {
        var options = new WorkerOwnedOptions
        {
            Projects = [new() { ProjectId = 8 }, new() { ProjectId = 9 }],
            Agents = [new() { Id = "codex-a", Provider = "codex", ProjectIds = [8], WorkKinds = ["dev"] },
                      new() { Id = "codex-b", Provider = "codex", ProjectIds = [8, 9], WorkKinds = ["qa", "qa_review"] }],
        };
        Assert.Single(options.Candidates(8, "dev"));
        Assert.Single(options.Candidates(9, "dev"));
        Assert.Empty(options.Candidates(10, "dev"));
        Assert.Equal("codex-b", Assert.Single(options.Candidates(8, "qa")).Id);
        Assert.Equal(6, options.Subscriptions().Count());
        Assert.DoesNotContain((8, "dev_review"), options.Subscriptions());
    }

    [Fact]
    public void Proposal_combines_grill_and_ticket_and_qa_review_is_not_code_review()
    {
        var proposal = WorkPlanner.Prompt("proposal", "{}");
        Assert.Contains("ONE responsibility", proposal);
        Assert.Contains("decision='ask'", proposal);
        Assert.Contains("decision='finalize'", proposal);
        var qa = WorkPlanner.Prompt("qa", "{}");
        Assert.Contains("INDEPENDENT QA Task", qa);
        Assert.Contains("deployment_steps", qa);
        Assert.Contains("Review the QA WORK", WorkPlanner.Prompt("qa_review", "{}"));
    }

    // -- P3 regression: Node ↔ Server error protocol is JSON, not a substring
    // search. The delivery loop's call site must read ``detail.reason`` to
    // decide between AckAndDrop (ghost, new-token, 404), Forbidden (try
    // next candidate), Conflict (terminal reconcile), and Unexpected
    // (EnsureSuccessStatusCode). The substring fallback stays as a safety
    // net for any non-JSON response shape.
    [Fact]
    public void Claim_409_with_ghost_work_row_reason_classifies_as_ack_and_drop()
    {
        var body = """{"detail":{"reason":"ghost_work_row","state":"available","entity_type":"","entity_id":0}}""";
        var outcome = ClaimFailureClassifier.Classify(HttpStatusCode.Conflict, body);
        Assert.Equal(ClaimFailureClassifier.Kind.AckAndDrop, outcome.Kind);
        Assert.Equal(ClaimFailureClassifier.Reasons.GhostWorkRow, outcome.Reason);
    }

    [Fact]
    public void Claim_404_classifies_as_ack_and_drop_even_without_detail_body()
    {
        var outcome = ClaimFailureClassifier.Classify(HttpStatusCode.NotFound, "");
        Assert.Equal(ClaimFailureClassifier.Kind.AckAndDrop, outcome.Kind);
        Assert.Equal("row not found on server", outcome.Reason);
    }

    [Fact]
    public void Claim_plain_409_without_reason_substring_falls_back_to_legacy_label()
    {
        // The substring fallback guards against a non-JSON response or a
        // future schema change. ``work_failed`` is one of the known terminal
        // reasons the loop must keep honoring even if the envelope moves.
        var outcome = ClaimFailureClassifier.Classify(HttpStatusCode.Conflict,
            "work failed; manual reconciliation required (work_failed)");
        Assert.Equal(ClaimFailureClassifier.Kind.Conflict, outcome.Kind);
        Assert.Equal(ClaimFailureClassifier.Reasons.WorkFailed, outcome.Reason);
    }
}
