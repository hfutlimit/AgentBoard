# Proposal — Agent 记忆自动加载（get_project_memory / append_agent_memory MCP 工具）

**status**: in_review

## 背景

Epic 78（AgentRun 执行器与主动推送闭环）Story 101-105 已交付执行器框架、Launcher/Trigger、
状态机驱动与 RunStatus 枚举对齐；但 Agent 被拉起执行任务时，「跨会话上下文」仍缺失——
Agent 每次会话都从零开始，无法复用团队约定、历史踩坑与用户偏好。

现有 `Document.type=memory` 只是「一份文档」，没有 Agent 自动取用/沉淀的语义入口：
- 无头 Agent（Codex/WorkBuddy/Qoder）要读项目记忆必须知道文档 id 并手工拼装；
- 会话中学到的新约定没有标准途径写回，下次会话依然健忘。

Story 107 把 memory 文档升维为 Agent 的「跨会话大脑」：**会话启动自动加载，会话中随手沉淀**。

## 方案（纯 MCP 客户端增量，零 REST / DB 契约变更）

复用既有 `/api/documents` REST 层（`_doc_list` / `_doc_create` / `_doc_update` helpers），
新增 2 个 MCP 工具：

1. `get_project_memory(project_id, agent=None)`：
   - 拉取项目全部 `type=memory` 文档；
   - `agent` 给定时仅返回「项目级 + 该 Agent 专属」记忆（Agent 级隔离）；
   - 返回 `documents` 列表 + `combined` 拼接文本，Agent 会话启动一次加载。

2. `append_agent_memory(project_id, content, agent=None)`：
   - 目标 title：项目级 `项目记忆` / Agent 级 `Agent 记忆 · {agent}`；
   - **幂等累积**：同名 title 已存在 → `PATCH` 续写 content；否则 `POST` 新建；
   - 返回目标文档 id / appended / content_length。

分层约定（零 DB 变更，title 前缀隔离）：
- 项目级：`项目记忆`（团队规范 / 约定 / 踩坑，所有 Agent 共享）；
- Agent 级：`Agent 记忆 · {agent}`（某 Agent 个性 / 擅长领域，按 agent 隔离）。

对标 Mem0 / Zep，但长在 PM 里、与任务闭环打通——「越用越懂你的项目」。

## 验收

- [x] 2 个工具注册为 MCP 工具（`list_tools` 可见）
- [x] 首次 append 创建、二次 append 同一 title 幂等累积（同一文档）
- [x] get 返回 combined 含累积内容与标题标注
- [x] Agent 级隔离：agent=A 取不到 agent=B 专属记忆，双方共享项目级
- [x] AST 静态护栏无未定义调用（Epic 97 防 `_api` 漏改复发）
- [x] Playwright E2E：Web 文档 Tab 可见 MCP 写入的 memory 文档，0 报错
- [x] 回归无新增失败；未触碰端口 18001 / docker
