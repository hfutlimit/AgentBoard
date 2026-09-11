# Proposal: WorkflowRun + Active Workflows Overview（AI Team Live Operations Dashboard）

> 状态：需求已确认（2026-09-11，用户拍板 A+ 方案 + 7 slice 全做 + 完整 event 契约 15+ 种）
> 前置：openspec/changes/ticket-flow-story-agent-20260809（Story 状态机 + 自动处理 + 评审下沉）
> 关联：Epic 122（Agent 协作闭环）/ Epic 123（设计评审流）/ Epic 78（Run 状态机）

## 问题

AgentBoard 当前 Story workflow 已完整（用户确认 → MQ `story.confirmed` → worker 拉 agent → design → impl → review → done），
但**用户视角严重缺位**：

| 视角 | 现状 | 问题 |
|---|---|---|
| 项目 Overview | 4 个 metric card + 速度图 + 焦点 Epic | 看不到"AI Team 正在做什么" |
| Story 详情 | status 文本 + task 列表 + status_history | 没有"事件流"语义，phase 切换不可见 |
| AgentRun | schedule 触发专用（`schedule_id` NOT NULL） | 跟 Story workflow 完全两套抽象，没有统一"执行实例" |
| WorkflowEvent | 不存在 | MQ 事件是 ephemeral 通知，无持久化 |

这是 AgentBoard 与普通 Jira 类产品的最大差异点——**AI Team 协作可视化**。没有它，AgentBoard 只是"会跑 Agent 的 Jira"。

## 目标

1. **WorkflowRun 抽象**：Story 确认后建一条 WorkflowRun，承载整个执行生命周期（design → development → qa → done）
2. **WorkflowEvent 流**：所有状态变化（Task 创建/分配/提交/重开、Review 申请/完成/打回、Phase 切换、阻塞、重试、心跳丢失）emit 持久化事件
3. **AgentRun 接入**：schedule_id 改 nullable，通过 `workflow_run_id` 与 AgentRun 关联；schedule / story 两种触发走同一套执行抽象
4. **Active Workflows 面板**：项目 Overview 新增实时卡片（阶段 rail + active executions + 最新事件）
5. **Workflow 详情页**：timeline 渲染事件流，DB authoritative，SignalR 推 ID 触发 refresh

## 非目标（后续 Change 承接）

- Workflow 类型扩展到 proposal / ticket / schedule 端到端（slice 3 之后渐进式接入，本 spec 只做 `workflow_type='story'` 起步）
- AI 智能摘要 / 异常检测 / 趋势预测 —— 本 spec 只做"如实展示"
- 跨 project 全局 Active Workflows 视图（本 spec 仅 project 级）
- 工作流模板市场（predefined workflow templates）

## 方案要点

### 数据模型

**新增 2 张表**：

`workflow_runs`（执行实例）：
- `id` / `project_id` (FK projects, index) / `workflow_type` (string, **v1 仅 `story`**，schema 不写死成 enum 留扩展性：未来可加 `schedule` / `proposal` / `ticket` / `deployment`)
- `story_id` (FK stories, nullable, index) / `schedule_id` (FK agent_schedules, nullable, index) —— v1 全 NULL
- `status` (queued|running|waiting|blocked|completed|failed|cancelled) — CheckConstraint
- `phase` (design|development|qa|null) — 与 status 解耦
- `current_task_id` (FK tasks, nullable)
- `reopened_from_run_id` (FK workflow_runs, nullable, index) —— Story reopen 时关联上一条 run（语义：reopen / retry 时用，不复用旧 run）
- `started_at` / `finished_at` / `last_activity_at`（3 个时间戳分开记，分别表达"创建/收尾/最近心跳"）
- `version` int default 1 — 乐观锁，SignalR 推送靠这个

**Terminal states 严格收敛**：`completed` / `failed` / `cancelled` 全是终态。`failed` **不能** `→ running` 重试——recoverable execution failure 在 WorkflowRun 仍 `running` 时通过 ExecutionAttempt（= `agent_runs` 新行）解决；retry terminal WorkflowRun 必须新建一条 run + 通过 `reopened_from_run_id` 关联。**核心原则：原 run 历史永久不变。**

`workflow_run_events`（事件流）：
- `id` / `workflow_run_id` (FK, index)
- `event_type` (15+ 种契约，详见 design §3)
- `phase` nullable / `state` nullable — phase×state 拆开，enum 不爆炸
- `actor_type` (user|agent|worker|system) / `actor_id` nullable
- `task_id` (FK tasks, nullable, index) / `agent_run_id` (FK agent_runs, nullable, index)
- `summary` text / `payload` json
- `created_at` (index)

**`agent_runs` 改造**（slice 3）：
- 加 `workflow_run_id` 字段（nullable, FK, index）
- 加 `stage_type` 字段（nullable，标识此 run 在 workflow 里的角色：design/dev/review/qa）
- `schedule_id` 改 nullable（Alembic 迁移：保留老行 schedule_id 真实值，workflow_run_id 留空）
- 抽象 `create_run` 拆为 `create_agent_run(trigger_type, workflow_run_id=, task_id=, agent_id=)`

### Event 契约（完整粒度 17 种）

生命周期：`workflow_started` / `workflow_completed` / `workflow_failed` / `workflow_cancelled` / `phase_changed` / `workflow_reopened`
Task：`task_created` / `task_assigned` / `task_started` / `task_submitted` / **`task_reopened`（仅单 run 内 review 打回用）**
Review：`review_requested` / `review_completed` / `changes_requested`
阻塞：`blocked` / `unblocked`
重试：`retry_scheduled`（仅 retry_scheduled payload 带 `retry_kind` metadata，三类：`execution` / `review_cycle` / `mq_delivery`；UI 上分桶展示，**MQ delivery 不进正常 UI**）
运维：`agent_heartbeat_lost` / `worker_lease_expired`

**`task_reopened` vs `workflow_reopened` 严格区分**：
- `task_reopened` —— 一个 WorkflowRun 内部，review 打回导致 task 重新进入 in_progress（同一个 run 内的事件循环）
- `workflow_reopened` —— 跨 run 生命周期：旧 run 已 `completed`/`failed`/`cancelled`，Story reopen / 用户 retry terminal workflow 时**新建一条** WorkflowRun，旧 run 历史永久不变，新 run 通过 `reopened_from_run_id` 关联

每种 event 的 phase / state / payload schema 在 design §3 列全表。

### API（4 个端点）

```http
GET  /api/projects/{project_id}/workflow-runs?status=active
GET  /api/workflow-runs/{run_id}
GET  /api/workflow-runs/{run_id}/events?before_id=&limit=
GET  /api/workflow-runs/{run_id}/executions
```

详见 design §4。

### 前端

**Overview 新增 Active Workflows 卡片**（design §5）：
- 阶段 rail 5 态语义：`✓ completed` / `● active` / `○ pending` / `⚠ blocked` / `× failed`（不显示百分比 progress bar）
- 当前 phase + status + elapsed
- active executions 列表（agent 名 + task 摘要 + 相对时间）
- 最新事件一行（"Codex started implementation · 42 sec ago"）
- **active-workflows DTO 必须内嵌 latest_event + active_executions 摘要**，避免 N+1 请求

**Workflow 详情页**（design §5）：
- 顶部：阶段 rail + 当前 phase + 总时长
- CURRENT 区：active executions 详细列表
- TIMELINE 区：倒序事件流（最近在上），每条带 icon + actor + 摘要
- 折叠区：execution details / agent output / review findings

### 实时推送

复用 `proposal-realtime.service.ts` 模式（pull truth + push ID）：

```
DB transaction
   ↓
WorkflowRun/Event 写入
   ↓
SignalR broadcast workflow.changed {project_id, workflow_run_id, version}
   ↓
Angular 收到 → 调 REST 拉详情
   ↓
渲染面板
```

**核心原则**：DB 是 authoritative，SignalR 只带 ID + version，前端不做业务推理。

### 兼容性

- 旧 `POST /api/schedules/{sid}/runs` 继续工作（`trigger_type='schedule'` 自动写 `workflow_run_id` null + `schedule_id` 保留）
- 旧 `agent_runs.schedule_id` 保留原值，nullable 化
- `workflow_type` schema 不写死成 enum（v1 仅 `story` 实际写入，但 `schedule` / `proposal` / `ticket` / `deployment` 字段允许；**原则：schema 可扩展，实现只支持 story**）
- v1 严格不做 cancel / pause / resume / retry 端点（走原 Story `set_status` 即可，避免 premature execution control API）

## 验收

### 单元测试
- `test_workflow_run_state_machine.py`：状态迁移合法表 + **`failed` 终态不可达 running** + phase transition graph + version 乐观锁
- `test_workflow_event_contract.py`：event_type 枚举校验 + 必填字段 + payload schema + **`task_reopened` vs `workflow_reopened` 严格区分**
- `test_agent_run_nullable_migration.py`：schedule_id nullable 化 + 老行验证 + 新增 workflow_run_id
- `test_workflow_run_phase_transition_graph.py`：显式 transition graph 校验（`design→development` / `development→qa` / `qa→development`），禁止任意 phase mutation

### 集成测试
- `test_story_to_workflow_run.py`：confirmed → WorkflowRun 创建 → 状态变化写 Event → API 查询
- `test_workflow_run_phase_progression.py`：design → development → qa → development 重工
- `test_retry_idempotency.py`：3 种 retry（MQ delivery / Execution / Review cycle）各自计数，**不共用 retry counter**
- `test_workflow_run_reopen.py`：Story reopen → 新 WorkflowRun + `reopened_from_run_id` 关联 + 旧 run 状态不变
- `test_active_workflows_no_n_plus_1.py`：list 端点一次返回所有 summary，**不**触发 per-run 子查询（断言 DTO 完整）

### E2E（tests/e2e/ 下新增）
- `test_e2e_active_workflows_panel.py`：项目 Overview 看到 Active Workflows 卡片
- `test_e2e_workflow_detail_timeline.py`：详情页 timeline 完整（17 种 event 至少出现 5 种）
- `test_e2e_workflow_realtime_push.py`：SignalR workflow.changed → REST refresh 全链路
- `test_e2e_workflow_reopen.py`：Story reopen 触发新 run，UI 显示 "Reopened from Run #N"

### 性能
- active-workflows 查询（项目 1000 story）< 200ms（DB 索引覆盖，**避免 N+1**）
- workflow_run_events 列表（200 条/页）< 100ms

### 兼容
- 旧 schedule API 行为不变（参数签名 + 返回结构）
- agent_runs 旧数据迁移后能查能跑
- 旧 Task status_history 不变（WorkflowEvent 是新增维度，不是替换）

## 切片（7 个，按顺序）

1. **workflow_runs + workflow_run_events 表 + Alembic 迁移**（纯 schema + 索引 + 状态机 + reopened_from_run_id）
2. **Task/Review/QA 状态变化写 WorkflowEvent**（hook 到 `service.set_status` / `claim_development_task` / `submit_task_for_review` / `review_task`；新增 `emit_workflow_event` helper；含 `workflow_reopened` event + phase transition graph）
3. **agent_runs 接入**（schedule_id nullable + workflow_run_id + stage_type + 旧行迁移 + create_agent_run 抽象）
4. **active-workflows + detail + events + executions 4 个 API**（feature router，**list 端点内嵌 latest_event + active_executions 避免 N+1**）
5. **Angular Active Workflows panel + detail timeline 组件**（services + components + e2e，rail 5 态）
6. **SignalR workflow.changed + Angular realtime 订阅**（后端 broadcast + 前端 service）
7. **reconciliation 工具 + 完整 E2E 套件**（DB ↔ MQ 状态对照 + CLI 脚本 + dod_registry）

每 slice 独立可测、独立 commit、独立 push。**README Status 在 UI 真正出来前只标 "Workflow Run foundation" 而非 "Overview implemented"，避免假绿。**
