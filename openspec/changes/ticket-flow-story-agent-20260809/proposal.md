# Proposal: Ticket 全流程 —— Story 工单化 + Agent 自动处理 + Agent 池可见性/心跳

> 状态：需求已确认（2026-08-09，基于 8 项 Open Questions 用户决策）
> 前置：`docs/proposal-ticket-flow-confirmed.md`（Proposal→Ticket 异步转化，切片 1+2 已落地）
> 关联：Epic 96（Proposal 澄清闭环）、Epic 122（Agent 协作/评审闭环）、Epic 123（设计评审流）、文档 #60（Story 自动建 design/实现 task）

## 背景

Proposal 澄清闭环与 Proposal→Ticket 异步转化（文档 #59）已交付：用户 grill 收敛 → 点击「生成 ticket」→ worker 拉起 agent 经 MCP 创建 epic/story/task/bug。但 **ticket 生成之后的执行阶段仍是半手动**：

| 环节 | 现状 | 目标 |
|---|---|---|
| Story 状态机 | 9 态，无强制迁移，PATCH 任意跳 | 强制 TRANSITIONS + 状态历史 |
| Story 评审态 | pending_review/ready（Story 级评审闭环） | **去掉**，评审职责下沉 Task 层（design 评审已有） |
| 用户闸门 | 无（创建即 backlog） | 新增 confirmed 态：用户确认要做 → 触发流程 |
| Task 执行 | 依赖人工/手动调 MCP | Story 确认后 agent **自动处理** design→开发→review→测试 |
| Agent 可见性 | 仅 MCP list_agents，前端无视图 | 前端 Agent 池视图（数量/在线/心跳/角色） |
| Agent 可用性 | online 靠 agent 自报心跳，worker 不探测 | worker 定时心跳：CLI 探测判活 |

## 目标

1. **Story 状态机工单化**：去掉 `pending_review`/`ready`；新增 `confirmed`（用户确认闸门）；强制 `STORY_TRANSITIONS`（复用 Task 单步查表模式）；新增 Story 状态历史表；
2. **确认后自动执行**：`confirmed` 触发 MQ `story.confirmed` → worker 消费 → CLI 拉起 agent → 经现有 MCP 工具自动推进（**约定：先完成 design task 再实现 task**，`_task_needs_design`+`transitions_for` 已强制）；
3. **Worker 定时心跳**：定时（60s）对 agents 表逐 agent 跑 `cli_command` 探测（8s 超时），成功置 online / 失败置 offline，不阻塞主循环；
4. **前端 Agent 池视图**：用户可见自己注册的 agent 数量、名称、角色、在线状态、last_heartbeat；
5. **评审运营视图适配**：`get_review_stats` 去除 Story 级统计（Task 侧完整保留），前端面板移除 Story 区块。

## 方案要点

- **Story 状态集合 9→8**：`backlog, confirmed, todo, in_progress, in_review, verifying, done, blocked`；`STORY_STATUS_SQL`/`STORY_REVIEW_STATUSES` 同步更新；
- **强制迁移**：service 新增 `STORY_TRANSITIONS` 字典 + `set_story_status()`（单步查表 + blocked 全向可达，**不做** previous_status 恢复，Story 解除 blocked 仅限 → todo/in_progress）；`update_story` 的 status 校验接入迁移表；
- **状态历史**：新建 `story_status_history`（id/story_id/from_status/to_status/changed_by/reason/created_at），与 `task_status_history` 同构，不动存量表；
- **确认触发链路**（参考 proposal ticket 模式）：`POST /api/stories/{sid}/confirm`（backlog→confirmed）→ MQ `story.confirmed` → worker `handle_story_confirmed`：全量重放 Story+Task 上下文 → `SubprocessAgentInvoker`（env `AGENTBOARD_WORKER_AGENT_CMD`）→ 转化模式提示词 → agent 经 MCP `set_status`/`claim_development_task`/`submit_task_for_review`/`review_task` 等**既有工具**推进，幂等（状态回查防重放）；
- **评审闭环收敛到 Task 层**：Story 的 `assign_reviewer`/`review_story` 端点与 MCP 工具标记 deprecated（返回 400「Story 评审已下线，评审在 Task 层进行」）；`workflow_worker` 的 story.created→assign_reviewer / story.ready 分支移除，改接 `story.confirmed` → 广播可用任务；`_vote_majority`/`scan_review_timeouts`/`get_review_stats` 的 story 分支移除；
- **Worker 心跳**：新增 `_agent_heartbeat_loop(60s)`：`list_agents` → 逐 agent 探测 `cli_command`（subprocess timeout=8s，兼容 `--version` 类子命令；无 cli_command 跳过，依赖自报心跳）→ 成功调 heartbeat API 置 online / 失败置 offline；探测异常仅日志不阻塞；
- **Ticket 三来源确认**（无需新开发）：① proposal 最后一步用户决定生成（已有）② 用户直接创建 Story（已有）③ 用户经 agent 用 MCP 创建（已有 `create_story` 等工具）；
- **前端**：侧栏/工作台新增 Agent 池面板（复用 `GET /api/agents`）；Story 详情「确认开始」按钮（backlog→confirmed）+ 状态 chips 去 pending_review/ready；review-ops-panel 移除 Story 区块。

## 验收

- 单测：`test_story_status_machine.py`（迁移/历史/confirm 触发/废弃端点 400）+ `test_worker_heartbeat.py`（探测判活/降级）+ 改造 `test_epic122_*` 系列（Story 评审用例迁至 Task 或移除）全部通过；
- 回归：既有 503 级单测零失败（除预存在 env 顺序污染项）；前端 vitest 全绿；
- E2E：Story 确认 → agent 自动推进 design→实现 task 全链路可观测；Agent 池面板渲染在线/离线；
- 零新增依赖；不触碰 18001；MCP 工具契约只增不删（deprecated 保留）。

## 切片

- 切片 1：Story 状态机（models/迁移/service/api/mcp 废弃分支）
- 切片 2：确认触发 + worker 编排（MQ 事件/worker/提示词/幂等）
- 切片 3：worker 心跳探测循环
- 切片 4：前端（Agent 池视图/Story 确认按钮/chips/review 面板）+ 测试与部署
