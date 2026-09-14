# Tasks — Agent Runtime Contract & Reconciler

**status**: draft
**date**: 2026-09-14
**Epic**: AgentBoard Epic 168（project 3）
**设计真源**: 本目录 `design.md`；MCP 设计任务 **1741**；看板镜像 doc（`type=design`，挂 Epic 168）
**执行纪律**: 设计评审已定「四个 P0 冻结前不领 446–459」。本文件落地后，P0-1（本设计）完成，其余三项见 Epic 169 与本文件 §前置。

---

## 前置（P0，冻结后才开工）

- [x] **P0-1 补 Epic 168 设计真源** ← 本目录
- [ ] **P0-2 Memory / learnings / knowledge 三层边界** → Epic 169 design.md §2.5（已冻结）
- [ ] **P0-3 Handoff 单一所有权**：453 只留恢复动作，schema 与加载归 457（看板已改）
- [ ] **P0-4 分面数以五面为准**（proposal 已改，Handoff 不进 memory）
- [ ] **P0-5 镜像 story 空任务**：阻塞 1742 / 1752；1741 承接本设计；1751 承接 doc 205

---

## Slice 1: 状态轴接线 + 首批事件（story **446a**）

**目标**：把 WorkflowRun 状态机接进 production，并补 3 个终态事件。**这是整个 Epic 的地基。**

- [ ] 按 design §2.2 (B) 设 `complete_workflow_run()` / `fail_workflow_run()` / `cancel_workflow_run()`（迁移 + emit 真实 payload）
- [ ] payload 来源按 design §2.2 表，**禁止填默认值编造**
- [ ] 接入真实路径：Story 完成 / 评审通过 / 取消
- [ ] 让 `transition_workflow_run_phase` 在 production 可达（`phase_changed` 现在是测试专属）
- [ ] 补 `task_created`（`confirm_story` 只发了 `workflow_started`）
- [ ] 集成测试：断言 `workflow_runs.status` 真的会变化，3 个终态可达
- [ ] 单测：payload 全部过 `validate_workflow_event_payload`

**commit**: `feat(workflow): wire the run status axis and emit terminal events`

---

## Slice 2: health 与 status 解耦（story 447）

- [ ] 迁移：`agent_runs.health`（`healthy/stale/dead`）+ 索引 `(status, health)`，双后端可逆
- [ ] `is_stale` 降为派生兼容位
- [ ] 拆分 `scan_stale_agent_runs`：观测（写 health）与动作（替换）分离，中间 emit
- [ ] 仅 `dead` 触发 release lease + 新 attempt；`stale` 只告警
- [ ] 补 `agent_heartbeat_lost` 生产者（payload 形状需与契约对齐，见 §遗留）
- [ ] 单测：`status` 与 `health` 独立变化

**commit**: `feat(scheduling): split agent health from run status`

---

## Slice 3: Reconciler 编排（story 448）

- [ ] 按 design §3.1 编排既有 recoverer（**不合并、不删除**）
- [ ] 补第 5 步 emit（`worker_lease_expired` 的生产者落在这里）
- [ ] 单测：一趟运行的每步各一条断言（含 emit）
- [ ] 单测：无待恢复项时是廉价 no-op
- [ ] **移除「删除重复入口」这条验收**（既有端点保留为人工/诊断入口）

**commit**: `refactor(worker): make maintenance a reconciler that orchestrates recoverers`

---

## Slice 4: 剩余事件生产者（story **446b**）

- [ ] slice 2 延后的 5 类：`task_assigned` / `task_submitted` / `review_requested` / `review_completed` / `changes_requested`
- [ ] 定 `task_started` / `blocked` / `unblocked` 是否需要在 workflow 事件里表达（避免与 Task `status_history` 重复）
- [ ] 单测：每类事件可触发且 payload 合规

**commit**: `feat(workflow): emit the deferred task and review events`

---

## Slice 5: progress 历史 → activity → UI（story 450 / 451 / 452）

- [ ] 450：`agent_run_reports` 追加表 + `report_task_progress` **双写**（保留 `last_progress_*` 作控制面最新态，design 已冻结）
- [ ] 451：`agent_run_activities` 独立存储 + **短留策略**（design 冻结：不做第三条流的载体）
- [ ] 452：Control Center 只读列表版（worker → agents → 心跳）；与 Epic 156 Worker-owned 页的关系按 design §5，**不重做、不合并**

**commit**: `feat(observability): progress history, activity stream and control center`

---

## Slice 6: 协议归档（story 449，**最后做**）

- [ ] 清点 register / claim / heartbeat / progress / finish 的**现有**面（REST + MCP 两套）
- [ ] 对照 `Cursor / Codex / MiniMax / Qwen / WorkBuddy` 五个 adapter 列不符合项
- [ ] 读 Epic 78 doc 9/10（执行器 + 推送）对照，**不重造定义**
- [ ] 产出 `docs/contracts/agent-runtime-contract-v1.md` + 差距表

**commit**: `docs(contracts): record the agent runtime contract v1`

---

## 遗留（需另开决策，不阻塞上面）

- **`agent_heartbeat_lost` 的 payload 形状**：契约现有必填键是 agent-probe 形状（`agent_id` / `last_seen_at` / `probe_message`），而 run 级 stale 需要 run 形状（`run_id` / `task_id` / `last_progress_at`）。**契约与产者不匹配**，需拍板：改 payload 还是新增一类。Slice 2 前必须定。
- **阈值数值**：30/60min 保持不变；等 story 450 有了报告粒度再评估。

## 完成定义（DOD）

- production 路径上 run status 真的迁移，19 类事件无孤儿
- health 与 status 独立可观测，仅 dead 触发替换
- 一趟 Reconciler 的行为由单测完整钉死（含 emit）
- 既有端点与人工恢复链**未被破坏**
