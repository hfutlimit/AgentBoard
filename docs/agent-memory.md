# AgentBoard — Agent Project Memory

> 项目专属知识沉淀。由 Mavis（agent）维护。跨会话累积，新会话启动时优先读本文件。
>
> 写入规则参考 Mavis memory 三段式：**规则 → 证据/原因 → 适用场景**。

---

## Proposal Worker: FakeAdapter（2026-08-21）

**规则 → Worker 想跑起来不依赖 workbuddy / MiniMax / codex 外部 CLI 时，加 `FakeAdapter`。**

- **证据/原因**：
  - 本地开发机一般没装 workbuddy / MiniMax / codex 三个 CLI；不装任何一个都会让相应 adapter 实际跑消息时炸。
  - 现有 `FakeAgentAdapter`（`workers/AgentBoard.ProposalWorker.Tests/Fixtures/FakeAgentAdapter.cs`）只是测试 fixture，不在生产代码里。
  - 2026-08-21 实装：新增 `workers/AgentBoard.ProposalWorker/Agents/FakeAdapter.cs`，`dotnet build` 0 警告 0 错误，后台启动后 `GET /health` 显示 4 个 agent（含 fake）均 `registered: true`。
- **做法**：
  1. `workers/AgentBoard.ProposalWorker/Agents/FakeAdapter.cs` 新增，类签名：
     ```csharp
     public sealed class FakeAdapter : IAgentAdapter
     {
         public string AgentType => "fake";
         public async Task<AgentExecutionResult> ExecuteAsync(ExecutionContext context, CancellationToken ct)
         {
             await Task.Yield(); // 让 dispatcher 走真正的异步路径
             // 构造 action: "ask" 的 JSON 返回
         }
     }
     ```
     不走 `IProcessExecutor`，无外部进程依赖。
  2. `Options.cs` 的 `AgentsOptions` 加 `public AgentOptions Fake { get; set; } = new() { Command = "" };`
  3. `appsettings.json` 的 `Agents` 段加 `Fake` 子段（`Command: ""`、`TimeoutMinutes: 1`、`MaxCapturedOutputChars: 20000`、`ApiKeyEnv: ""`）
  4. `Program.cs` 加 `builder.Services.AddSingleton<IAgentAdapter, FakeAdapter>();`
- **验证**：本地 `dotnet run --project workers/AgentBoard.ProposalWorker`，日志出现 `Registered agents: [workbuddy, minimax, codex, fake]`；`http://127.0.0.1:58240/health` 返回 `agents.fake.registered: true`。
- **不踩坑点**：
  - `IOptions<AgentsOptions>` 注入到 `FakeAdapter` 是 OK 的，即使 Fake 段我们根本不读字段（保持选项 schema 一致）。
  - `AgentAdapterRegistry` 走的是 `IEnumerable<IAgentAdapter>`，新增的 `FakeAdapter` 会被自动收进 registry，不需要额外改 registry 代码。
  - RabbitMQ URL 留空会让 `RabbitMqConsumerService` 报 `RabbitMq:Uri is required; consumer is disabled` —— 这是预期的，consumer 不会启用，HTTP portal 仍可访问。
- **适用场景**：
  - 本地无 CLI 时的 smoke 测试
  - CI 环境跑 dispatch 链路
  - e2e 跑流程又不想 mock 真 CLI
  - 演示 / 教学

---

## AgentBoard MCP 服务端的临时问题（2026-08-21）

**规则 → `mcp__agentboard__append_agent_memory` 当前报 `_MEMORY_PROJECT_TITLE` / `_MEMORY_AGENT_PREFIX` 未定义错误，无法使用。**

- **临时方案**：在 `docs/agent-memory.md` 维护项目级沉淀，Mavis 会话启动时先读这个文件。
- **不阻塞**：项目代码改动本身不依赖 mcp 记忆功能。
- **后续**：等服务端修好（看错误信息像模板渲染变量未注入）。
