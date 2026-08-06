# Proposal：S1 M3 MCP 评审工具 + Workflow 分配器 Worker

> ID: agent-collab-s1-m3-20260807 · Epic 122 多 Agent 自动协作闭环 · Story 230 S1 切片

## 背景与问题

S1 M1（Task 1006）交付了 Agent 注册表 / Story 评审状态机与 REST API；
S1 M2（Task 1008）交付了 Workflow 事件总线泛化（WorkflowMessage / WorkflowTopology /
Pika+InMemory broker / WorkflowPublisher）。但两者之间缺一层「把事件总线接进真实业务
流程、把评审能力暴露给 Agent」的胶水：

1. **事件源未接入**：`story.created` / `review.requested` / `story.ready` 等事件
   常量已定义、发布器已可用，但 API 层没有任何一处发布——MQ 总线目前是死总线；
2. **无自动分配器**：Story 创建后没有消费者把 `story.created` 变成
   `assign-reviewer` 调用，「Story 创建触发评审指派」的切片验收不成立；
3. **评审能力未进 MCP**：M1 只做了 REST 端点，Agent 无法通过 MCP 注册身份、
   心跳保活、拉取评审任务、投出评审票——多 Agent 闭环缺少入口。

## 目标

1. **事件源接入（api.py）**：Story 创建 → 广播 `story.created`；指派成功 →
   定向 `review.requested`（reviewer 的 Agent 队列）；评审 approve → 广播
   `story.ready`；reject → 广播 `review.rejected`（带轮次）；Story 评论 →
   定向 `comment.replied`。发布一律 best-effort，MQ 故障不影响 REST。
2. **分配器 Worker（新模块 workflow_worker.py）**：消费广播队列，把
   `story.created` 转为 `POST /api/stories/{sid}/assign-reviewer`（随机指派在线
   reviewer，CAS 幂等）；`story.ready` / `task.*` 事件预留切片 2 开发任务分配；
   MQ 未配置回退 DB 轮询兜底。
3. **MCP 评审工具（mcp_server.py）**：`agent_register` / `agent_heartbeat` /
   `agent_deregister` / `list_agents` / `review_story` / `list_review_tasks`
   六个工具封装既有 REST 端点。

## 非目标

- 不实现开发任务分配（切片 2 `task.available` 认领），仅预留事件处理位；
- 不实现 Agent 侧 CLI/订阅逻辑（Agent 自行订阅定向队列）；
- 零既有 REST/DB 契约变更；零新增第三方依赖。

## 验收

- 单测覆盖事件源接入（创建/指派/评审/评论四类端点的事件发布断言）；
- WorkflowConsumer 对 `story.created` 调 assign-reviewer（mock HTTP）、轮询幂等；
- MCP 工具 AST 注册 + 真实栈直调全链路；
- 既有测试零回归；不触碰 18001。
