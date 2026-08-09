# Tasks: Ticket 全流程 —— Story 工单化 + Agent 自动处理 + Agent 池可见性/心跳

## 状态

### 切片 1：Story 状态机（后端核心）

- [ ] models.py：移除 `STORY_REVIEW_STATUSES`，`STORY_STATUS_SQL` 更新 8 值（backlog/confirmed/todo/in_progress/in_review/verifying/done/blocked）
- [ ] 新增 `StoryStatusHistory` 模型（story_id/from_status/to_status/changed_by/reason/created_at）
- [ ] 迁移 `w_story_ticket_flow`：SQLite batch_alter + MariaDB 双路径；存量数据映射 pending_review→todo、ready→confirmed
- [ ] service.py：`STORY_TRANSITIONS` 字典 + `set_story_status()`（单步查表 + blocked 特判 + 写历史 + 发 MQ）
- [ ] service.py：`update_story` 状态校验升级为迁移校验；新增 `confirm_story`（CAS backlog→confirmed + 发 story.confirmed）+ `list_story_status_history`
- [ ] service.py：移除/废弃 Story 评审分支（assign_reviewer story 分支、review_story story 分支、list_review_tasks story 过滤、_reassign_story_reviewer、_vote_majority story 分支、scan_review_timeouts story 分支、get_review_stats story 统计）
- [ ] api.py：新增 `POST /api/stories/{sid}/confirm`、`GET /api/stories/{sid}/status-history`；`assign-reviewer`/`review` 返回 400 提示评审在 Task 层
- [ ] mcp_server.py：新增 `story_confirm` 工具；`review_story` 返回错误提示（契约保留）
- [ ] workflow_worker.py：移除 story.created→assign_reviewer / story.ready→broadcast；新增 story.confirmed→广播可用 Task
- [ ] 单测 tests/test_story_status_machine.py（迁移/历史/confirm CAS/废弃端点 400）

### 切片 2：Agent 自动处理编排

- [ ] mq.py 事件白名单新增 `story.confirmed` / `story.status_changed`
- [ ] worker.py：`build_story_context` + `_build_story_prompt`（转化模式：design task 先行铁律 + 现有 MCP 工具推进）+ `handle_story_confirmed`（回查/幂等/失败评论兜底）
- [ ] worker.py：`run_mq_forever` 接线 story.confirmed 消费
- [ ] 单测 tests/test_story_confirm_flow.py（InMemoryBroker + 假 invoker：编排/幂等/失败兜底）

### 切片 3：Worker 心跳探测

- [ ] worker.py：`agent_heartbeat_once()`（cli_command shlex 解析 + `--version` 探测 8s 超时 + 成功 heartbeat/失败 deregister）+ `_agent_heartbeat_loop(60s)` 挂入 maintenance
- [ ] 单测 tests/test_worker_heartbeat.py（判活/超时降级/无 cli_command 跳过/异常不阻塞）

### 切片 4：前端 + 部署

- [ ] models.ts：新增 `AgentRow` 接口；`ReviewStats` story 字段可选
- [ ] app.ts：Agent 池面板数据加载（GET /api/agents + timeAgo）；Story 确认按钮动作；statusLabel/statusColor/statusSemanticClass 增 confirmed 删 pending_review/ready；状态历史时间线
- [ ] app.html/app.css：Agent 面板 + Story 确认按钮 + 状态历史折叠区；review-ops-panel 移除 Story 区块
- [ ] 前端 vitest + E2E（Agent 池渲染 / Story 确认流 / chips 映射）
- [ ] 回归：既有 503 级单测零失败；docker restart api/web（不触碰 18001）
- [ ] git commit + push

## 验收记录

- （待填）

## 硬约束

- 零新增依赖；MCP/REST 契约只增不删（废弃端点保留返回 400）；不触碰 18001；
- 「先完成 design task 再实现 task」由服务端 `_task_needs_design`+`transitions_for` 强制，提示词仅强调不新增校验；
- Ticket 三来源（proposal 生成 / 用户直接创建 / agent 经 MCP 创建）均已具备，本切片不新增来源。
