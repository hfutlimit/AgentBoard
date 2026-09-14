# Agent Runtime Contract & Reconciler — 变更提案

**status**: draft
**date**: 2026-09-14
**来源**: 2026-09-14 外部架构 review 第 1-3 轮 + 设计评审（canvas `epic-168-169-review`）
**关联**: AgentBoard Epic 168（本变更）、Epic 169（记忆侧，见 `agent-handoff-and-project-memory-20260914`）
**载体**: MCP 设计任务 **1741**（镜像 story 445 自动生成的设计任务）；看板镜像 doc `Agent Runtime Contract & Reconciler — 架构设计`

---

## 为什么做

Epic 168 的 8 个业务 story（446–453）**共享一批尚未落纸的接口**：health 枚举语义、事件生产者口径、Reconciler 的组合方式、以及和记忆侧的分界。没有这份设计，8 个 story 会各自发明一套。

**Story 449 不能替代它**：449 是「S1/S2 落地后的协议归档」，描述的是**新口径**，事后才写得准；开工前需要的是**现在就要冻结的架构设计**。

## 核心事实（全部已用代码核实）

| 断言 | 结果 | 证据 |
|---|---|---|
| 事件流基本是空的 | 19 类声明 / 5 类有 emit 调用点 | `event_contract.py` vs 全库 emit 调用 |
| 其中 production 可达的只有 **2 类** | `workflow_started` / `task_reopened` | 其余 emit 都在 helper 内，而 helper 无 production 调用点 |
| WorkflowRun 状态轴完全没接线 | `run.status` 只在 `transition_workflow_run_status:95` 被赋值，而它只有 tests 调用 | production 里 run 永远停在 `queued` |
| 无 production 调用点的 helper | `transition_workflow_run_status` / `transition_workflow_run_phase` / `reopen_story_workflow` / `record_retry_scheduled` | `git grep` 仅命中 def 与 tests |
| `phase_tracker.py` 未落地 | 目录只有 `__init__ / event_contract / models / service / state_machine` | tasks.md slice 2 计划内 |
| 观测即改状态 | `scan_stale_agent_runs` → 直接 `soft_takeover_run`（改 Task 状态 + 释放租约） | `scheduling/service.py:2869 → :2981` |
| progress 只有最新态 | `last_progress_at` / `last_progress_note`(500) 覆盖式单字段 | `scheduling/models.py:69-75` |
| F2 已修最小形态 | `maintenance.scan_stale_runs` 已接进 `coordinator.poll_once` + `worker._maintenance_loop` | commit `c49daca` |

## 改什么（本设计的冻结项）

1. **health 与 status 正交**（story 447）：health 枚举 `healthy / stale / dead`，health 变化**不改** `status`；只有 `dead` 触发替换。
2. **事件生产者口径**（story 446a/446b）：状态轴接线（含 3 个终态）+ slice 2 延后的 5 类 + `task_created`。
3. **Reconciler 是编排者不是替代者**（story 448）：编排已有 recoverer 并**补 emit**，不合并、不删除 proposal / 人工裁定恢复入口。
4. **三条流的分工**（与 Epic 169 共用同一份口径，见其 design.md §2/§2.5）：workflow events（状态）/ activity（过程，短留）/ knowledge（晋升日志 → `learnings`）。
5. **依赖顺序**（按接口依赖，不按 story 号）。

## 影响范围

`features/workflow_runs/*`（状态轴接线 + emit）、`features/scheduling/*`（health 枚举、scan/takeover 拆分）、`processors/maintenance.py` + `coordinator.py`（Reconciler 编排）、新迁移（health 列）、`features/documents`（Control Center 读面）。

## 不做什么

- 不重命名事件类型（会作废 19 类 Literal + 必填 payload 表 + 8 项冻结决策 + 132 个既有测试 + slice 4-7 计划）。
- 不改 WorkflowRun 层级（`tasks.md` 决策 3 已冻结 `Story → WorkflowRun → AgentRun(attempt)`）。
- 不把 activity / knowledge 塞进 `workflow_run_events`。
- 不删 proposal / 人工裁定恢复入口（doc 173 是另一条链）。
- 不改 30min warn / 60min takeover 默认值本身。

## 成功判据

- production 路径上 `workflow_runs.status` 真的会变化（集成测试断言 3 个终态可达）。
- 19 类事件**没有「既无生产者也无计划」的孤儿**（当前 9 类）。
- agent 长时间无报告时，`status` 不变、`health` 变 `stale`/`dead`。
- 一趟 Reconciler 的行为由单个单测完整钉死（含 emit）。
