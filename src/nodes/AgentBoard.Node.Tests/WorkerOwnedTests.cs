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
        var next = WorkPlanner.Next(8, "task", item)!;
        Assert.Equal("dev", next.Kind);
        Assert.Equal("original-dev", next.TargetAgent);
        Assert.Equal(9, next.DiscussionId);
        Assert.Equal(2, next.Iteration);
        var prompt = WorkPlanner.Prompt("dev", JsonSerializer.Serialize(new { item, discussion }));
        Assert.Contains("Leave ALL files and HEAD unchanged", prompt);
        Assert.Contains("position='agree'|'disagree'|'clarify'", prompt);
        Assert.DoesNotContain("commit your changes", prompt);
        Assert.Equal("agentboard.work.v2.project.8.dev.agent.ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb",
            WorkerWorkKinds.AgentQueue(8, "dev", "a"));
        Assert.Equal(7, WorkerWorkKinds.All.Count);
    }

    [Fact]
    public void Review_requires_discussion_before_rejection_and_old_approval_cannot_create_bugs()
    {
        Assert.Contains("Never reject or create bugs before discussion", WorkPlanner.Prompt("dev_review", "{}"));
        var output = new JsonObject { ["decision"] = "approve" };
        WorkPlanner.AddQaFollowup("qa_review", output, JsonSerializer.SerializeToElement(new { }));
        Assert.Null(output["qa_followup"]);
        output["decision"] = "confirm";
        WorkPlanner.AddQaFollowup("qa_review", output,
            JsonSerializer.SerializeToElement(new { discussion = new { subject = "review_findings" } }));
        Assert.Null(output["qa_followup"]);
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
    public void Confirmed_failed_qa_plans_bugs_and_retest_on_worker_from_latest_own_evidence()
    {
        var context = JsonSerializer.SerializeToElement(new
        {
            item = new { id = 5, title = "QA greeting", description = "Original acceptance" },
            discussion = new { subject = "qa_defects" },
            evidence = new[]
            {
                new { work_id = 4, task_id = 5, kind = "qa", result = new { tests_passed = true, defects = Array.Empty<object>() } },
                new { work_id = 7, task_id = 5, kind = "qa", result = new { tests_passed = false,
                    defects = new object[] { new { title = "Fix 500", description = "GET /greet: expected 200, actual 500" } } } },
                new { work_id = 8, task_id = 99, kind = "qa", result = new { tests_passed = true, defects = Array.Empty<object>() } },
            },
        });
        var output = JsonNode.Parse("""{"decision":"confirm","summary":"verified together"}""")!.AsObject();
        WorkPlanner.AddQaFollowup("qa_review", output, context);
        var plan = output["qa_followup"]!;
        Assert.Equal(7, plan["source_work_id"]!.GetValue<long>());
        Assert.Equal("Fix 500", Assert.Single(plan["bugs"]!.AsArray())!["title"]!.GetValue<string>());
        Assert.Contains("Original acceptance", plan["retest"]!["description"]!.GetValue<string>());
        Assert.Contains("ALL linked bug Tasks", plan["retest"]!["description"]!.GetValue<string>());
        output.Remove("qa_followup");
        output["decision"] = "reject";
        WorkPlanner.AddQaFollowup("qa_review", output, context);
        Assert.Null(output["qa_followup"]);
        output["decision"] = "approve";
        WorkPlanner.AddQaFollowup("dev_review", output, context);
        Assert.Null(output["qa_followup"]);
    }

    [Fact]
    public void Passed_qa_does_not_plan_bug_tasks()
    {
        var context = JsonSerializer.SerializeToElement(new
        {
            item = new { id = 5 },
            evidence = new[] { new { work_id = 7, task_id = 5, kind = "qa", result = new { tests_passed = true } } },
        });
        var output = new JsonObject { ["decision"] = "approve" };
        WorkPlanner.AddQaFollowup("qa_review", output, context);
        Assert.Null(output["qa_followup"]);
    }

    [Fact]
    public void Acceptance_checkboxes_do_not_become_duplicate_development_tasks()
    {
        var plan = WorkPlanner.TicketPlan("Greeting", "## Implementation\n- [ ] Build greeting\n## Acceptance\n- [ ] Default name\n- [ ] Chinese\n- [ ] HTTP 404");
        var tasks = plan["tasks"]!.AsArray();
        Assert.Equal(3, tasks.Count);
        Assert.Single(tasks, t => t!["type"]!.GetValue<string>() == "dev");
        Assert.Contains("ticket_plan", WorkPlanner.Prompt("proposal", "{}"));
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

    [Fact]
    public void Unsupported_historical_task_does_not_abort_reconciliation()
    {
        var item = JsonSerializer.SerializeToElement(new { id = 42, type = "ticket", status = "todo",
            ready = true, story_status = "in_progress" });
        Assert.Null(WorkPlanner.Next(8, "task", item));
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

    [Theory]
    [InlineData("design", "todo", "design")]
    [InlineData("design", "in_review", "design_review")]
    [InlineData("dev", "in_review", "dev_review")]
    [InlineData("bug", "todo", "dev")]
    [InlineData("bug", "in_review", "dev_review")]
    [InlineData("qa", "todo", "qa")]
    [InlineData("qa", "in_review", "qa_review")]
    public void Worker_plans_task_and_matching_review(string type, string status, string expected)
    {
        var item = JsonSerializer.SerializeToElement(new { id = 42, type, status, story_status = "in_progress",
            ready = true, needs_human_confirmation = false, review_round = 2 });
        var work = WorkPlanner.Next(8, "task", item);
        Assert.Equal(expected, work!.Kind);
        Assert.Equal(2, work.Iteration);
    }

    [Fact]
    public void Worker_does_not_offer_blocked_or_human_gated_work()
    {
        var item = JsonSerializer.SerializeToElement(new { id = 42, type = "qa", status = "todo", story_status = "in_progress",
            ready = false, review_round = 0 });
        Assert.Null(WorkPlanner.Next(8, "task", item));
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
}
