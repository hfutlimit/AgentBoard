# Tasks: Ticket 全流程 —— Story 工单化 + Agent 自动处理 + Agent 池可见性/心跳

## 状态

### 切片 1：Story 状态机（后端核心）

- [x] models.py：移除 `STORY_REVIEW_STATUSES`，`STORY_STATUS_SQL` 更新 8 值（backlog/confirmed/todo/in_progress/in_review/verifying/done/blocked）
- [x] 新增 `StoryStatusHistory` 模型（story_id/from_status/to_status/changed_by/reason/created_at）
- [x] 迁移 `w_story_ticket_flow`：SQLite batch_alter + MariaDB 双路径；存量数据映射 pending_review→todo、ready→confirmed
- [x] service.py：`STORY_TRANSITIONS` 字典 + `set_story_status()`（单步查表 + blocked 特判 + 写历史）+ `confirm_story`（CAS backlog→confirmed）+ `complete_story`（自动收尾）+ `list_story_status_history`
- [x] service.py：`update_story` 状态校验升级为迁移校验；Story 评审分支废弃（assign_reviewer/review_story 抛「评审已下线」）
- [x] api.py：新增 `POST /api/stories/{sid}/confirm`、`POST /api/stories/{sid}/complete`、`GET /api/stories/{sid}/status-history`；`assign-reviewer`/`review` 返回 422
- [x] mcp_server.py：新增 `confirm_story` / `story_status_history` 工具（18001 下次部署生效）
- [x] workflow_worker.py：移除 story.created→assign_reviewer / story.ready→broadcast；story.confirmed 仅 ack（执行由 Proposal Worker 轮询兜底）
- [x] 单测 tests/test_story_status_machine.py（14 passed）

### 切片 2：Agent 自动处理编排

- [x] mq.py 事件白名单新增 `story.confirmed`
- [x] worker.py：`build_story_context` + `_build_story_prompt`（执行铁律：design 先行 + 现有 MCP 工具推进）+ `handle_story`（节流 30s/失败评论/连续 3 次 → blocked）+ `_story_scan_loop` + poll_once/run_mq_forever 接线
- [x] worker.py：`ACTION_STORY_HANDLED` 决策协议
- [x] 单测 tests/test_worker_heartbeat.py 编排部分（22 passed 含心跳）

### 切片 3：Worker 心跳探测

- [x] worker.py：`_probe_cli`（shlex + --version 8s 超时）+ `agent_heartbeat_once`（成功 heartbeat/失败 deregister/无 cli_command 跳过）+ `_agent_heartbeat_loop(60s)` 挂入 maintenance + poll_once 节流
- [x] WorkerConfig 新增 heartbeat_interval / heartbeat_timeout（env 可配）
- [x] 单测 test_worker_heartbeat 心跳部分（判活/超时降级/跳过）

### 切片 4：前端 + 部署

- [x] models.ts：`AgentRow` / `StoryStatusHistoryRow` 接口；Status 联合加 confirmed
- [x] app.ts：Agent 池视图（loadAgents/agentRoles/goAgents/侧栏徽标）+ Story 确认（confirmStory）+ 状态历史（toggle/loadStoryStatusHistory）+ statusLabel/Color/Semantic 增 confirmed 删 pending_review/ready
- [x] app.html：侧栏 Agents 入口 + Agent 池视图 + Story 确认按钮 + 状态历史折叠区
- [x] app.css：sidebar-nav-badge / agent-card 系列 / story-confirm-row / status-history
- [x] api.service.ts：confirmStory/completeStory/storyStatusHistory/listAgents
- [x] 前端构建 main-V6KSZFCV.js 已同步 web/static
- [x] 回归：核心单测 326 passed / 9 skipped（含新增 22 + 改造 epic122 系列 40+）
- [x] git commit + push（见 commit）

## 验收记录

- pytest：核心单测 326 passed / 9 skipped（test_backend_flow、test_crud_smoke、epic122 全系列、epic96、proposal_ticket_flow、test_story_status_machine、test_worker_heartbeat 等 28 文件）；
- 新增 tests/test_story_status_machine.py（14）+ tests/test_worker_heartbeat.py（22）；
- 改造：test_epic122_agent_review / s1_m3_worker_mcp / s1_m3_workflow_events / s2m1 / s3m1 / s3m2 / s3m3 / s4m2 → Story 评审语义迁移到 Task 侧；
- REST 冒烟（18099）：confirm（backlog→confirmed CAS+幂等）→ complete（自动收尾 done）→ status-history（backlog→confirmed→done）→ agents 列表；废弃 assign-reviewer/review 返回 422「评审已下线」；非法迁移 400；
- alembic upgrade head 到 x6y7z8a9b0c1 成功（SQLite 双后端）；迁移存量映射 pending_review→todo / ready→confirmed 实测通过；
- 前端构建成功（main-V6KSZFCV.js，CSS budget warning 为既有非错误）。

## 硬约束

- 零新增依赖；MCP/REST 契约只增不删（废弃端点保留返回 422）；不触碰 18001（MCP 工具确认后代码就位，下次部署生效）；
- 「先完成 design task 再实现 task」由服务端 `_task_needs_design`+`transitions_for` 强制，提示词仅强调不新增校验；
- Ticket 三来源（proposal 生成 / 用户直接创建 / agent 经 MCP 创建）均已具备，本切片不新增来源。
