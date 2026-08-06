# Tasks：S1 M3 实现清单

> ID: agent-collab-s1-m3-20260807 · 全部 `[x]` 表示已交付

## 实施步骤

- [x] **事件源接入**：`agentboard/api.py` — import mq 事件常量；`create_story` → 广播 `story.created`；`assign_story_reviewer` → 定向 `review.requested`（user_id→agent_id 内联解析，退化广播）；`review_story` → approve `story.ready` / reject `review.rejected`（ref_id=round）；`create_story_comment` → 定向 `comment.replied`
- [x] **分配器 Worker**：`agentboard/workflow_worker.py` 新建 — `WorkflowConsumerConfig.from_env` / `WorkflowConsumer`（handle_message 分发、`_assign_reviewer` 容错语义、`run_mq_forever` 消费广播队列 + MQ 回退轮询、`run_poll_once` / `run_forever` 幂等扫描）+ CLI（--mq / --loop / --once）
- [x] **MCP 评审工具**：`agentboard/mcp_server.py` — `agent_register` / `agent_heartbeat` / `agent_deregister` / `list_agents` / `review_story` / `list_review_tasks` 封装既有 REST 端点，走 `_http`
- [x] **测试**：`tests/test_epic122_s1_m3_workflow_events.py`（事件源接入四端点发布断言，InMemory broker 注入）+ `tests/test_epic122_s1_m3_worker_mcp.py`（WorkflowConsumer mock HTTP + 轮询幂等 + MCP 工具 AST 注册 + 真实栈直调全链路）
- [x] **OpenSpec**：`openspec/changes/agent-collab-s1-m3-20260807/{proposal,design,tasks}.md`

## 验证记录

- pytest（S1 M3 用例 + 既有回归全绿，详见当日 memory）
- Epic 97 AST 护栏（未定义调用 / `/api` 前缀）零违规
- Playwright E2E 回归 0 错误
- 部署：docker cp 注入后端 + restart api（未触碰 18001）

## 状态

Task 1009 → in_review；Story 230 / Epic 122 持续推进（下一步：S1 整体验收 或 切片 2 开发任务分配）。
