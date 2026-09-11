# WorkflowRun + Active Workflows Overview — 任务清单

**status**: pending
**date**: 2026-09-11
**owner**: mavis（开发）/ 用户（评审 + 拍板）
**前置**: ticket-flow-story-agent-20260809 已落地

> 7 slice 一口气交付。每 slice 独立 commit + push；通过即推进下一切片。
> 每 slice 完成后追加 `tests/e2e/dod_registry.py` + 更新 `docs/e2e-plan.md` section 14 + 更新 README Status block。

---

## Slice 1: workflow_runs + workflow_run_events 表 + Alembic 迁移

**目标**：schema 落地，两张表 + 索引 + 状态机（`failed` 严格终态）+ 迁移测试。

- [ ] `agentboard/features/workflow_runs/__init__.py`
- [ ] `agentboard/features/workflow_runs/models.py`：定义 `WorkflowRun` + `WorkflowRunEvent`（含 CheckConstraint + Index；`workflow_type` 不加 CHECK，schema 可扩展，v1 application 层仅接受 `story`）
- [ ] `agentboard/features/workflow_runs/models.py`：`WorkflowRun.reopened_from_run_id` FK 字段（self-reference，nullable，ON DELETE SET NULL，index）
- [ ] `agentboard/features/workflow_runs/schemas.py`：Pydantic 序列化层
- [ ] `agentboard/features/workflow_runs/migrations/m5n6o7p8q9r0_create_workflow_runs_and_events.py`：Alembic 双后端迁移（SQLite + MariaDB）
- [ ] `agentboard/core/application/service.py`：新增 `WORKFLOW_RUN_TRANSITIONS`（`failed` 终态 set()）+ `workflow_run_transition()` 函数
- [ ] `agentboard/core/application/service.py`：新增 `WORKFLOW_PHASE_TRANSITIONS` 显式 graph（`design→development` / `development→qa` / `qa→development`）+ `can_transition_phase()` 校验
- [ ] 单测 `tests/test_workflow_run_state_machine.py`：合法迁移 / 非法迁移报错 / **`failed` 终态不可达 running** / 取消不可重试
- [ ] 单测 `tests/test_workflow_run_phase_transition_graph.py`：显式 graph 校验（合法路径 + 非法 mutation）
- [ ] 双后端迁移测试 `tests/test_migration_workflow_runs.py`：upgrade + downgrade round-trip
- [ ] `docs/migrations-changelog.md` 追加条目
- [ ] **README Status 标 "Workflow Run foundation implemented"**（**不**写 "Overview implemented"）

**commit**: `feat(workflow): add workflow_runs + workflow_run_events tables (slice 1)`

---

## Slice 2: Task/Review/QA 状态变化写 WorkflowEvent

**目标**：service 层所有状态变化点 emit WorkflowEvent；emit helper 抽出来；phase transition graph 落地；新增 `workflow_reopened` event。

- [ ] `agentboard/features/workflow_runs/service.py`：`emit_workflow_event()` helper（含 event_type 白名单校验 + workflow_runs.last_activity_at 更新 + version++）
- [ ] `agentboard/features/workflow_runs/event_contract.py`：17 种 event_type 枚举 + 必填字段 schema（**`task_reopened` vs `workflow_reopened` 严格区分**）
- [ ] `agentboard/core/application/service.py` hook 点：
  - `confirm_story()` → emit `workflow_started` + `task_created`（每个 task）
  - `set_status()` done→in_progress → emit `task_reopened`（**单 run 内 review 打回用**）
  - Story reopen（done → in_progress 跨 run） → **新建** WorkflowRun + emit `workflow_reopened` event（`reopened_from_run_id` 关联旧 run）
  - `claim_development_task()` / `apply_for_task()` → emit `task_assigned`
  - `submit_task_for_review()` → emit `task_submitted`
  - `assign_task_reviewer()` → emit `review_requested`
  - `review_task(approve)` → emit `review_completed`
  - `review_task(reject)` → emit `changes_requested`
  - Agent execution retry → emit `retry_scheduled`（`retry_kind='execution'`）
  - Review cycle retry → emit `retry_scheduled`（`retry_kind='review_cycle'`）
  - MQ delivery retry → emit `retry_scheduled`（`retry_kind='mq_delivery'`，**仅 diagnostics**）
- [ ] `agentboard/features/workflow_runs/phase_tracker.py`：phase transition graph 校验函数（按 `WORKFLOW_PHASE_TRANSITIONS`）
- [ ] 单测 `tests/test_workflow_event_contract.py`：17 种 event_type 枚举校验 + `task_reopened` vs `workflow_reopened` 区分
- [ ] 集成测试 `tests/test_story_to_workflow_run.py`：confirmed → WorkflowRun 创建 → task 状态变化 → event 全部落库
- [ ] 集成测试 `tests/test_task_to_workflow_event.py`：claim→start→submit→review→done 全链路
- [ ] 集成测试 `tests/test_workflow_run_phase_rework.py`：`qa→development` 重工合法 + `phase_changed` event
- [ ] 集成测试 `tests/test_workflow_run_reopen.py`：Story reopen → 新 WorkflowRun + `reopened_from_run_id` 关联 + 旧 run 状态不变 + `workflow_reopened` event
- [ ] 集成测试 `tests/test_workflow_run_failed_terminal.py`：`failed` 终态校验 + retry 只能新建 run
- [ ] `tests/e2e/dod_registry.py` 追加 slice 2 入口

**commit**: `feat(workflow): emit workflow events from task/review state changes (slice 2)`

---

## Slice 3: agent_runs 接入（schedule_id nullable + workflow_run_id + stage_type）

**目标**：agent_runs 与 workflow_runs 通过外键关联；旧 schedule API 行为不变。

- [ ] `agentboard/features/scheduling/models.py`：加 `workflow_run_id` + `stage_type` 字段；`schedule_id` 改 nullable
- [ ] `agentboard/features/scheduling/migrations/s7t8u9v0w1x2_agent_runs_workflow_nullable.py`：Alembic 双后端迁移
- [ ] `agentboard/features/scheduling/service.py`：`create_run()` 内部转 `create_agent_run(trigger_type='schedule', ...)`；新增 `create_agent_run()` 抽象
- [ ] `agentboard/features/workflow_runs/service.py`：`link_agent_run(workflow_run_id, agent_run_id, stage_type=)` helper
- [ ] Story worker 接入：`_execute_auto_story_request()` 内创建 `WorkflowRun` + `create_agent_run(trigger_type='workflow', ...)`
- [ ] MCP 工具 `agent_run_create(workflow_run_id, task_id, agent_id, stage_type=)` 新增
- [ ] MCP 工具 `agent_run_set_stage(agent_run_id, stage_type)` 新增
- [ ] 单测 `tests/test_agent_run_nullable_migration.py`：schedule_id nullable + 老行保留 + FK 校验
- [ ] 集成测试 `tests/test_agent_run_workflow_link.py`：create_agent_run 抽象 + 旧 API 兼容
- [ ] `tests/e2e/dod_registry.py` 追加 slice 3 入口

**commit**: `feat(workflow): link agent_runs to workflow_runs via workflow_run_id (slice 3)`

---

## Slice 4: active-workflows + detail + events + executions 4 个 API

**目标**：4 个端点上线，feature router 落地。**active-workflows 端点必须内嵌 latest_event + active_executions 摘要，避免 N+1**。

- [ ] `agentboard/features/workflow_runs/router.py`：4 个端点
  - `GET /api/projects/{project_id}/workflow-runs`（**内嵌 latest_event + active_executions**，**不**为每条 run 触发 per-run 查询）
  - `GET /api/workflow-runs/{run_id}`
  - `GET /api/workflow-runs/{run_id}/events`
  - `GET /api/workflow-runs/{run_id}/executions`
- [ ] `agentboard/features/workflow_runs/service.py`：4 个查询函数
  - `list_active_workflows(project_id)`：单 SQL 主表 + 批量 LATERAL JOIN 拉 latest_event + active_executions（DB 查询次数 ≤ 3）
  - `get_workflow_run(run_id)`：含 stage 聚合
  - `list_workflow_events(run_id, before_id=, limit=)`
  - `list_workflow_executions(run_id)`
- [ ] `agentboard/api.py` 注册新 router
- [ ] `agentboard/features/mcp/workflow_runs.py` MCP 工具：
  - `workflow_run_get(workflow_run_id)`
  - `workflow_run_list_events(workflow_run_id, before_id=, limit=)`
  - `workflow_run_emit_event(workflow_run_id, event_type, summary, payload=)`
- [ ] `agentboard/mcp_server.py` 注册新 MCP 工具
- [ ] 权限：走 `project_access_middleware`（read: project member / write: project member）
- [ ] 单测 `tests/test_workflow_run_api.py`：4 个端点 200/404/403 语义
- [ ] 集成测试 `tests/test_workflow_run_api_integration.py`：项目 1000 story 下查询 < 200ms（pytest-benchmark）
- [ ] 集成测试 `tests/test_active_workflows_no_n_plus_1.py`：list 端点单次返回所有 summary + **断言 DB 查询次数 ≤ 3**
- [ ] `docs/contracts/` 追加 OpenAPI 片段
- [ ] `tests/e2e/dod_registry.py` 追加 slice 4 入口

**commit**: `feat(workflow): add active-workflows and detail API (slice 4)`

---

## Slice 5: Angular Active Workflows panel + detail timeline 组件

**目标**：项目 Overview 新增卡片 + 新增 workflow detail 页面 + timeline 渲染。**阶段 rail 5 态（completed/active/pending/blocked/failed），不显示百分比 progress bar**。

- [ ] `frontend/src/app/services/workflow-runs.service.ts`：4 个 API client + 缓存
- [ ] `frontend/src/app/models/workflow.ts`：`WorkflowRun` / `WorkflowRunEvent` / `AgentRun` 接口
- [ ] `frontend/src/app/active-workflows-card/active-workflows-card.component.ts`：卡片组件
  - 阶段 rail 5 态：`✓ completed` / `● active` / `○ pending` / `⚠ blocked` / `× failed`
  - active executions 列表（agent 名 + task 摘要 + 相对时间）
  - 最新事件一行（"Codex started implementation · 42 sec ago"）
  - `reopened_from_run_id` 非 null 时显示 "Reopened from Run #N" 提示
- [ ] `frontend/src/app/active-workflows-card/active-workflows-card.html`
- [ ] `frontend/src/app/active-workflows-card/active-workflows-card.css`
- [ ] `frontend/src/app/active-workflows-card/active-workflows-card.spec.ts`：组件单测（vitest）
- [ ] `frontend/src/app/workflow-detail/workflow-detail.component.ts`：详情页（头部 + rail + current + timeline + reopened 关联区）
- [ ] `frontend/src/app/workflow-detail/workflow-detail.html`
- [ ] `frontend/src/app/workflow-detail/workflow-detail.css`
- [ ] `frontend/src/app/workflow-detail/workflow-detail.spec.ts`
- [ ] `frontend/src/app/overview-tab/overview-tab.html`：velocity chart 下方插入 `<app-active-workflows-card>`
- [ ] `frontend/src/app/overview-tab/overview-tab.ts`：注入 service + load 逻辑
- [ ] `frontend/src/app/app.routes.ts`：新增 route `workflow-runs/:id`
- [ ] vitest 全绿
- [ ] E2E `tests/e2e/test_e2e_active_workflows_panel.py`：Playwright 截图 + 元素断言
- [ ] E2E `tests/e2e/test_e2e_workflow_detail_timeline.py`：详情页 17 event 至少出现 5 种
- [ ] **README Status 升级为 "Active Workflows Overview implemented"**（仅在 slice 5 UI 真正出来后才升级）

**commit**: `feat(workflow): add Angular active workflows panel and detail timeline (slice 5)`

---

## Slice 6: SignalR workflow.changed + Angular realtime 订阅

**目标**：DB transaction 写完后 broadcast；前端收到 ID + version 后 refresh。

- [ ] `agentboard/realtime.py`：`broadcast_workflow_changed(workflow_run_id, version)` 函数
- [ ] `agentboard/features/workflow_runs/service.py`：`emit_workflow_event()` 写库后调 `broadcast_workflow_changed`
- [ ] `agentboard/main.py`：SignalR hub 注册新 event `workflow.changed`
- [ ] `agentboard/api.py`：`POST /api/projects/{pid}/workflow-runs/join-group`（前端订阅用，鉴权 project member）
- [ ] `frontend/src/app/services/workflow-realtime.service.ts`：复用 `proposal-realtime.service.ts` 的 SignalR connection，新增 `watchProject(projectId, onChanged)`
- [ ] `frontend/src/app/active-workflows-card/active-workflows-card.ts`：订阅 workflow.changed，收到后单独 refresh 该 workflow（不重拉列表）
- [ ] `frontend/src/app/workflow-detail/workflow-detail.ts`：订阅 workflow.changed，校验 version >= push.version，否则忽略（防乱序）
- [ ] vitest 全绿
- [ ] E2E `tests/e2e/test_e2e_workflow_signalr_push.py`：Playwright 监听 network + 验证 push 后 UI 自动更新
- [ ] `tests/e2e/dod_registry.py` 追加 slice 6 入口

**commit**: `feat(workflow): add SignalR workflow.changed push notification (slice 6)`

---

## Slice 7: reconciliation 工具 + 完整 E2E 套件

**目标**：DB ↔ MQ 状态对照 + 端到端 happy path 全覆盖 + dod_registry 收尾。

- [ ] `agentboard/features/workflow_runs/reconciliation.py`：
  - `reconcile_workflow_run(workflow_run_id)`：对照 DB 状态与最近 event 流，标记漂移
  - `reconcile_all_runs(project_id)`：批量扫描
  - CLI 入口 `python -m agentboard.workflow_runs.reconciliation --once`
- [ ] `agentboard/features/workflow_runs/maintenance.py`：reconciliation 周期（30 min）
- [ ] E2E `tests/e2e/test_e2e_workflow_full_lifecycle.py`：
  - 创建 Story → confirmed → 模拟 agent 全链路（design → review → dev → review → qa → done）
  - 验证 17 种 event 全部出现
  - 验证 phase 切换按 transition graph
  - 验证 active workflows 卡片实时更新
  - 验证详情页 timeline 完整
- [ ] E2E `tests/e2e/test_e2e_workflow_retry_idempotency.py`：
  - 模拟 MQ delivery retry（**不进 normal UI**，仅 diagnostics 可见）
  - 模拟 execution retry（计数独立）
  - 模拟 review cycle retry（计数独立）
- [ ] E2E `tests/e2e/test_e2e_workflow_reopen.py`：Story reopen 触发新 run + `reopened_from_run_id` 关联 + 旧 run 状态不变 + UI 显示 "Reopened from Run #N"
- [ ] E2E `tests/e2e/test_e2e_workflow_failed_terminal.py`：`failed` 终态不可达 running + retry 只能新建 run
- [ ] E2E `tests/e2e/test_e2e_workflow_reconciliation.py`：DB 状态与 event 流漂移时 reconciliation 报告
- [ ] 性能压测 `tests/perf/test_workflow_run_perf.py`：
  - active-workflows 查询（1000 story）< 200ms
  - workflow_run_events 列表（200 条）< 100ms
  - SignalR push 延迟 < 500ms
- [ ] `docs/e2e-plan.md` section 14 进度表更新
- [ ] `README.md` Status block 更新
- [ ] `tests/e2e/dod_registry.py` 追加 slice 7 入口
- [ ] 全量回归：`pytest -m e2e` 全绿 / `pytest`（unit + integration）全绿
- [ ] MCP 状态同步：WorkflowRun 相关 task 全部 in_review → done

**commit**: `feat(workflow): add reconciliation tool and full e2e coverage (slice 7)`

---

## 跨切片任务

- [ ] `openspec/changes/workflow-run-overview-20260911/` 三件套（proposal/design/tasks）已就位
- [ ] 每 slice 完成后：
  - [ ] `git add` + `git commit`（conventional commits）
  - [ ] `git push origin main`（无需用户确认，按用户 2026-08-18 工作流偏好）
  - [ ] 追加 `tests/e2e/dod_registry.py`
  - [ ] 更新 `docs/e2e-plan.md` section 14
  - [ ] 更新 `README.md` Status block（**严格：UI 出来前只标 "Workflow Run foundation"，slice 5 后才升级 "Active Workflows Overview"**）
- [ ] 任何踩坑 → 立刻 `agentboard` MCP `append_agent_memory` 写入项目记忆（按用户 2026-08-18 偏好）

## 关键决策冻结（spec 拍板，不再扩大讨论）

> 用户 2026-09-11 拍板 8 项决策已写进 proposal.md / design.md / tasks.md。后续实现如发现新约束，先尝试用现有约定解决，不要回头改 spec。

1. **`workflow_type` 首版只 `story`**：schema 不写死成 CHECK，application 层 v1 仅接受 `story`（**schema 可扩展，实现只支持 story**）
2. **`retry_kind` 三类**：`execution` / `review_cycle` / `mq_delivery`，**仅 event/diagnostic metadata，**不共用 retry counter**；MQ delivery 不进 normal UI
3. **`failed` 严格终态**：不可 `→ running`；recoverable failure 走 ExecutionAttempt（agent_runs 新行）+ WorkflowRun 仍 `running`；retry terminal = 新建 run + `reopened_from_run_id` 关联
4. **Phase transition graph 显式**：`design→development` / `development→qa` / `qa→development`；**禁止任意 mutation**；未来新 transition 走 `rework_requested` 显式入口
5. **Story reopen 新 WorkflowRun**：`reopened_from_run_id` 关联 + `workflow_reopened` event + 老 run 历史永久不变
6. **4 API 够用**：active-workflows / detail / events / executions；**不加** cancel/pause/resume/retry 端点；list DTO **内嵌** latest_event + active_executions 避免 N+1
7. **Rail 5 态**：`✓ completed` / `● active` / `○ pending` / `⚠ blocked` / `× failed`；**不**显示百分比 progress bar
8. **DoD 老路径**：dod_registry + e2e-plan + README Status + commit + push；slice 保持可独立验证，**README 不假绿**

---

## 完成定义（DOD）

所有 7 slice 的 checkbox 全部勾完 + 全量 e2e 通过 + 文档同步 + commit + push 完成，视为整个 Change 完成。

**完成后**：
- 项目 Overview Active Workflows 卡片可见
- 详情页 timeline 完整展示 17 种 event
- SignalR 实时推送 < 500ms
- 旧 schedule API 行为不变
- 双数据库迁移回滚可用
