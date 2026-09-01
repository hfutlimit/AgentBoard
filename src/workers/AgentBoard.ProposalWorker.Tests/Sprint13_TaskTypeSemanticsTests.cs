// SPDX-License-Identifier: MIT
using System.Text;
using AgentBoard.ProposalWorker;
using AgentBoard.ProposalWorker.Agents;
using AgentBoard.ProposalWorker.Execution;
using AgentBoard.ProposalWorker.Process;
using AgentBoard.ProposalWorker.Tests.Fixtures;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Xunit;

namespace AgentBoard.ProposalWorker.Tests;

/// <summary>
/// Sprint 13 — P0-1 + P0-2（2026-09-01 GPT review）。
///
/// P0-2：Design / QA / Dev 路由已特化（design→workbuddy / dev,bug→codex /
/// qa→workbuddy），但 Task prompt 原来只有一份 "implement + commit" 语义。
/// 现在 task_type 从 FastAPI dispatch 一路透传到 ExecutionContext，
/// <see cref="SharedAdapterHelpers.BuildWorkloadPrompt"/> 按 task_type 分
/// 三路执行语义。
///
/// P0-1：Worker 注册身份（per-agent AgentBoardToken）原来没有传进 CLI
/// 子进程。ProcessExecutor 会清空父进程环境，AgentBoard MCP server 读
/// AGENTBOARD_MCP_TOKEN / AGENTBOARD_API_URL —— adapter 现在显式注入，
/// 保证「注册身份 == 执行身份 == MCP API 身份」。
/// </summary>
public sealed class Sprint13_TaskTypeSemanticsTests
{
    // -------------------------------------------------------------------------
    // P0-2 — BuildWorkloadPrompt 按 task_type 分执行语义
    // -------------------------------------------------------------------------

    [Fact]
    public void Task_prompt_uses_design_semantics_for_design_task_type()
    {
        var prompt = SharedAdapterHelpers.BuildWorkloadPrompt(
            "test", MakeContext(WorkloadTypes.Task, taskType: "design"));

        Assert.Contains("Design task", prompt);
        // Design 阶段只产出设计：明确禁止写实现代码 / commit。
        Assert.Contains("NOT write implementation code", prompt);
        Assert.DoesNotContain("Implement the task", prompt);
    }

    [Fact]
    public void Task_prompt_uses_qa_semantics_for_qa_task_type()
    {
        var prompt = SharedAdapterHelpers.BuildWorkloadPrompt(
            "test", MakeContext(WorkloadTypes.Task, taskType: "qa"));

        Assert.Contains("QA task", prompt);
        // QA 只验证：明确禁止改实现 / commit fix。
        Assert.Contains("verification-only task", prompt);
        Assert.DoesNotContain("Implement the task", prompt);
    }

    [Fact]
    public void Task_prompt_uses_implementation_semantics_for_dev_task_type()
    {
        var prompt = SharedAdapterHelpers.BuildWorkloadPrompt(
            "test", MakeContext(WorkloadTypes.Task, taskType: "dev"));

        Assert.Contains("Task implementation", prompt);
        Assert.Contains("read code, make changes, run relevant tests, commit", prompt);
    }

    [Fact]
    public void Task_prompt_uses_implementation_semantics_for_legacy_null_task_type()
    {
        // Legacy 消息（无 task_type 字段）→ 默认 implementation 语义，
        // 与旧行为字节级兼容。
        var prompt = SharedAdapterHelpers.BuildWorkloadPrompt(
            "test", MakeContext(WorkloadTypes.Task, taskType: null));

        Assert.Contains("Task implementation", prompt);
        Assert.Contains("read code, make changes, run relevant tests, commit", prompt);
    }

    [Fact]
    public void Rework_prompt_scopes_design_rework_to_design_only()
    {
        // Rework 分支的 "Commit" 一刀切对 design 返工是错的 —— design
        // 返工只能改设计文档，不能开始写实现。
        var prompt = SharedAdapterHelpers.BuildWorkloadPrompt(
            "test", MakeContext(WorkloadTypes.Rework, taskType: "design", round: 1));

        Assert.Contains("design rework", prompt);
        Assert.Contains("refine the design/spec only", prompt);
    }

    [Fact]
    public void Rework_prompt_scopes_qa_rework_to_verification_only()
    {
        var prompt = SharedAdapterHelpers.BuildWorkloadPrompt(
            "test", MakeContext(WorkloadTypes.Rework, taskType: "qa", round: 1));

        Assert.Contains("QA rework", prompt);
        Assert.Contains("re-verify and correct the QA report", prompt);
    }

    // -------------------------------------------------------------------------
    // P0-2 — WorkflowMessage.Parse / Mapper 透传 task_type
    // -------------------------------------------------------------------------

    [Fact]
    public void WorkflowMessage_Parse_reads_task_type_field()
    {
        var payload = """{"event":"task.assigned","entity_type":"task","entity_id":7,"ts":"t","agent_type":"workbuddy","task_type":"design"}""";
        var msg = WorkflowMessage.Parse(Encoding.UTF8.GetBytes(payload));

        Assert.Equal("task.assigned", msg.Event);
        Assert.Equal("design", msg.TaskType);
    }

    [Fact]
    public void WorkflowMessage_Parse_tolerates_missing_task_type()
    {
        // Legacy 消息没有 task_type → null（prompt 退回 implementation 语义）。
        var payload = """{"event":"task.assigned","entity_type":"task","entity_id":7,"ts":"t","agent_type":"workbuddy"}""";
        var msg = WorkflowMessage.Parse(Encoding.UTF8.GetBytes(payload));

        Assert.Null(msg.TaskType);
    }

    [Fact]
    public void WorkflowMessageMapper_propagates_task_type_to_execution_request()
    {
        var mapper = new WorkflowMessageMapper(ThreeAgentRegistry());
        var msg = new WorkflowMessage(
            "task.assigned", "task", 7, null, "ts", "workbuddy", TaskType: "design");

        var req = mapper.MapToExecution(msg, source: "broadcast");

        Assert.Equal(WorkloadTypes.Task, req.WorkloadType);
        Assert.Equal("design", req.TaskType);
    }

    // -------------------------------------------------------------------------
    // P0-1 — Adapter 把 AgentBoard 身份注入 CLI 子进程环境
    // -------------------------------------------------------------------------

    [Fact]
    public async Task Codex_adapter_injects_per_agent_identity_into_cli_environment()
    {
        var executor = new RecordingExecutor();
        var options = new AgentsOptions
        {
            Codex = new AgentOptions
            {
                Command = Environment.ProcessPath!,
                AgentBoardToken = "tok-codex-per-agent",
            },
        };
        var agentboard = new AgentBoardOptions
        {
            ServerUrl = "http://127.0.0.1:58124",
            StartupToken = "tok-startup",
        };
        var adapter = new CodexAdapter(
            executor, Options.Create(options), Options.Create(agentboard),
            NullLogger<CodexAdapter>.Instance);

        await adapter.ExecuteAsync(
            MakeContext(WorkloadTypes.Task, taskType: "dev"), CancellationToken.None);

        // per-agent token 优先于 startup token —— MCP 调用（submit-review 等）
        // 必须以该 agent 注册时的同一个 user 身份发出，否则
        // task.assignee_id == current_user 校验失败。
        Assert.Equal("tok-codex-per-agent", executor.Spec!.Environment["AGENTBOARD_MCP_TOKEN"]);
        Assert.Equal("http://127.0.0.1:58124", executor.Spec.Environment["AGENTBOARD_API_URL"]);
    }

    [Fact]
    public async Task Codex_adapter_falls_back_to_startup_token_when_per_agent_token_empty()
    {
        var executor = new RecordingExecutor();
        var options = new AgentsOptions
        {
            Codex = new AgentOptions { Command = Environment.ProcessPath! },
        };
        var agentboard = new AgentBoardOptions
        {
            ServerUrl = "http://127.0.0.1:58124",
            StartupToken = "tok-startup",
        };
        var adapter = new CodexAdapter(
            executor, Options.Create(options), Options.Create(agentboard),
            NullLogger<CodexAdapter>.Instance);

        await adapter.ExecuteAsync(
            MakeContext(WorkloadTypes.Task, taskType: "dev"), CancellationToken.None);

        // 空 per-agent token → 回退 StartupToken，与 Worker startup
        // registration 的 fallback 语义一致。
        Assert.Equal("tok-startup", executor.Spec!.Environment["AGENTBOARD_MCP_TOKEN"]);
    }

    [Fact]
    public async Task WorkBuddy_adapter_injects_per_agent_identity_into_cli_environment()
    {
        var executor = new RecordingExecutor();
        var options = new AgentsOptions
        {
            WorkBuddy = new AgentOptions
            {
                Command = Environment.ProcessPath!,
                AgentBoardToken = "tok-wb-per-agent",
            },
        };
        var agentboard = new AgentBoardOptions
        {
            ServerUrl = "http://127.0.0.1:58124",
            StartupToken = "tok-startup",
        };
        var adapter = new WorkBuddyAdapter(
            executor, Options.Create(options), Options.Create(agentboard),
            NullLogger<WorkBuddyAdapter>.Instance);

        await adapter.ExecuteAsync(
            MakeContext(WorkloadTypes.Task, taskType: "design"), CancellationToken.None);

        Assert.Equal("tok-wb-per-agent", executor.Spec!.Environment["AGENTBOARD_MCP_TOKEN"]);
        Assert.Equal("http://127.0.0.1:58124", executor.Spec.Environment["AGENTBOARD_API_URL"]);
    }

    [Fact]
    public async Task Adapters_skip_identity_injection_when_no_token_configured()
    {
        // dev 环境（无鉴权）：两个 token 都空 → 不注入变量，
        // CLI 用默认行为跑。ServerUrl 为空也不注入 API URL。
        var executor = new RecordingExecutor();
        var options = new AgentsOptions
        {
            Codex = new AgentOptions { Command = Environment.ProcessPath! },
        };
        var agentboard = new AgentBoardOptions();
        var adapter = new CodexAdapter(
            executor, Options.Create(options), Options.Create(agentboard),
            NullLogger<CodexAdapter>.Instance);

        await adapter.ExecuteAsync(
            MakeContext(WorkloadTypes.Task, taskType: "dev"), CancellationToken.None);

        Assert.False(executor.Spec!.Environment.ContainsKey("AGENTBOARD_MCP_TOKEN"));
        Assert.False(executor.Spec.Environment.ContainsKey("AGENTBOARD_API_URL"));
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

    private static ExecutionContext MakeContext(
        string workloadType, string? taskType, long workloadId = 7, int round = 0) =>
        new(1, $"task:{workloadId}:{round}", workloadType, workloadId, round,
            "workbuddy", "{}", null, taskType);

    private sealed class RecordingExecutor : IProcessExecutor
    {
        public ProcessSpec? Spec { get; private set; }

        public Task<ProcessResult> ExecuteAsync(ProcessSpec spec, CancellationToken ct)
        {
            Spec = spec;
            return Task.FromResult(new ProcessResult
            {
                ExitCode = 0,
                Duration = TimeSpan.Zero,
            });
        }
    }
}
