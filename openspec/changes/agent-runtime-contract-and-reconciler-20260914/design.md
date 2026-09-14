# Design — Agent Runtime Contract & Reconciler

## 1. health 与 status 正交（story 447）

### 1.1 两个维度

| 维度 | 取值 | 回答 | 谁写 |
|---|---|---|---|
| `workflow_runs.status` / `tasks.status` | 既有状态机 | 业务上应该到哪一步 | 状态机（受控迁移） |
| `agent_runs.health`（新） | `healthy` / `stale` / `dead` | 这个 agent 现在还活着吗 | 观测层（reconciler 扫描） |

**硬约束**：health 变化**绝不**直接改 `status`。现状违反这条：`scan_stale_agent_runs`（`scheduling/service.py:2869`）观测到停滞就调 `soft_takeover_run`（:2981），后者直接 `run.status="failed"` + Task 回退 `todo` + 释放 assignment —— 中间没有 seam。

### 1.2 现状映射

- 现在的 `agent_runs.is_stale`（`scheduling/models.py:75`，布尔）= `health ∈ {stale, dead}` 的合并态，语义不足（分不清告警与可替换）。
- 新增 `health` 列后，`is_stale` 保留为 `health != 'healthy'` 的派生兼容位，避免破坏既有读面。

### 1.3 阈值与语义

| health | 判定 | 允许的动作 |
|---|---|---|
| `healthy` | `now - last_progress_at < warn_after` | 无 |
| `stale` | `warn_after ≤ 间隔 < takeover_after` | **只告警 / 只发事件**（不改 status，不释放租约） |
| `dead` | 间隔 ≥ `takeover_after` 且 run 仍 running | **允许替换**：释放租约 + 新 attempt |

默认值**保持** `PROGRESS_WARN_AFTER_SECONDS = 30*60` / `PROGRESS_TAKEOVER_AFTER_SECONDS = 60*60`（`scheduling/service.py:2805-2806`）。理由：LLM 推理 + 工具链天然分钟级；误报的代价是真实重派。数值可在有报告粒度（story 450）之后再调。

### 1.4 迁移

- `agent_runs.health VARCHAR(20) NOT NULL DEFAULT 'healthy'` + 索引 `(status, health)`；双后端（MariaDB + SQLite）可逆。
- 存量行：`is_stale=1` → `health='stale'`；其余 `healthy`。NULL `last_progress_at`（从未上报）按「自 run 开始即停滞」处理（沿用现有语义）。

## 2. 事件生产者口径（story 446a / 446b）

### 2.1 19 类账目（冻结）

| 分类 | 数量 | 事件 | 归属 |
|---|---|---|---|
| production 已达 | 2 | `workflow_started`、`task_reopened` | 已落地 |
| 有 emit 但 helper 无 production 调用点 | 3 | `phase_changed`、`workflow_reopened`、`retry_scheduled` | **446a** 接线后自然可达 |
| 正文明确延后（`b0c1b23`） | 5 | `task_assigned`、`task_submitted`、`review_requested`、`review_completed`、`changes_requested` | **446b** |
| 孤儿（无生产者且无计划） | 9 | `workflow_completed`、`workflow_failed`、`workflow_cancelled`、`task_created`、`task_started`、`blocked`、`unblocked`、`agent_heartbeat_lost`、`worker_lease_expired` | 446a（前 3 + `task_created`）/ 447（`agent_heartbeat_lost`）/ 448（`worker_lease_expired`）/ `task_started`・`blocked`・`unblocked` 见 §2.4 |

### 2.2 状态轴接线（446a 的地基）

`transition_workflow_run_status` 当前签名 `(s, run, *, to_status, set_finished_at=False)` **没有** reason/summary 入参，而终态事件必填 payload 需要：

| 事件 | 必填 payload | 来源（**不得编造**） |
|---|---|---|
| `workflow_completed` | `summary` / `duration_seconds` / `task_count` | `duration_seconds` 由 `started_at..now` 算；`task_count` 查该 story 的 task 数；`summary` 由**调用方传入**（无则不 emit，不得填默认值） |
| `workflow_failed` | `reason` / `failed_at_stage` / `last_error` | `failed_at_stage` 取 `run.phase`；`reason`/`last_error` 由调用方传入 |
| `workflow_cancelled` | `cancelled_by` / `reason` | 由调用方传入（`cancelled_by` 为 user id） |

**实现方式（二选一，推荐后者）**：

- (A) 扩 `transition_workflow_run_status` 签名，加 `reason` / `summary` / `cancelled_by` 可选参数，仅在 3 个终态且参数齐备时 emit。
- (B) **观测层与状态迁移层分离**：`transition_workflow_run_status` 保持纯状态应用；另设 `complete_workflow_run()` / `fail_workflow_run()` / `cancel_workflow_run()` 负责「迁移 + emit 真实 payload」。

推荐 (B) —— 与 health/status 正交（§1）是同一个原则，且让「谁在什么时候发什么事件」集中可审计。

### 2.3 接线前提

整条状态轴**当前没有任何 production 调用点**（全库只有 `transition_workflow_run_status:95` 写 `run.status`，而它只被 tests 调）。所以 446a 的第一件事不是「补 emit」，而是**把状态迁移接进真实路径**（Story 完成 / 评审通过 / 取消）。**先接线，再补事件** —— 否则只是给没人调的函数加副作用。

### 2.4 剩下 3 类的归属

`task_started` / `blocked` / `unblocked` 未分配：`blocked`/`unblocked` 已有 Task 侧 `status_history` 记录 who/why，是否需要在 workflow 事件里重复表达，由 446b 的实现在读完 `work_items/service.py set_status` 后定；`task_started` 与 `task_assigned` 语义相邻，避免重复表达。

## 3. Reconciler 是编排者，不是替代者（story 448）

### 3.1 必须编排的既有 recoverer（**只编排，不合并、不删除**）

| recoverer | 位置 | 负责 |
|---|---|---|
| `expire_stale_agent_heartbeats` / `expire_stale_worker_heartbeats` | `features/projects/service.py` | agent / worker presence |
| `scan_stale_agent_runs` | `features/scheduling/service.py:2869` | 进度停滞（**本轮已挂触发器**） |
| `reclaim_stale` / `reclaim_stale_stories` / `reclaim_stale_tasks` / `reclaim_stale_ticket_requests` | `processors/maintenance.py` | 租约超期 |
| `recover_failed`（提案）/ `recover_failed_proposals` | `processors/maintenance.py:110` / `features/proposals/service.py:956` | Agent 不可用导致的 failed 提案 |
| `sweep` | `processors/maintenance.py:132` | 自愈重投 |
| **人工裁定恢复（Epic 156 doc 173：generation / 输入冻结）** | — | ⚠ **不纳管**。那是人裁定的另一条链 |

### 3.2 Reconciler 的职责边界

一趟运行（可测试的纯函数式入口，传入 client/config，返回结构化 result）：

1. scan health（写 `health`）
2. renew / expire leases
3. detect timeout
4. recover（**调用上面已有的 recoverer**）
5. **emit events**（当前完全缺失的一步）
6. reschedule

**删除「删除重复入口」这条验收** —— 收敛是「由一个入口编排」，不是「只留一个实现」。既有端点（`POST /api/scheduling/scan-stale-runs` 等）保留为人工/诊断入口。

### 3.3 已有挂载点（不新建调度器）

- `processors/worker.py:625 _maintenance_loop`（`while not stop.wait(self.config.maintenance_interval)`）
- `processors/coordinator.py:422 poll_once()` 末尾维护段

## 4. 三条流的分工（与 Epic 169 共用口径）

`workflow_run_events`（状态，封闭枚举，长期）/ `agent_run_activities`（过程，开放集合，**短留**）/ knowledge（**落到既有 `learnings` 表**，晋升后长期）。

完整论证、`learnings` 列映射与晋升链见 `openspec/changes/agent-handoff-and-project-memory-20260914/design.md` §2 / §2.5。story 451 只管 activity，story 458 只管 knowledge 的写入面与晋升通道。

## 5. Story 452（Control Center）与既有 Worker UI 的关系

**已存在**：Epic 156 的 Worker-owned 本机任务记录页（MCP doc 168–172），运行在 **worker 本机 portal**（`src/nodes/AgentBoard.Node/WorkerOwned/ConfigurationPortal.html`），视角是「本机这个 Worker」。

**452 的视角是服务端**：跨 worker / 跨 agent 的全局运行态势（哪些 worker 上跑着哪些 agent、各自在干什么）。两者**不是替代关系**，但必须写清：

- 452 **不重做** Worker-owned 页面，也不把它并进来；
- 两者共享同一份数据面（`Worker` / `AgentInstance` / `Agent` / `AgentRun`），只是过滤维度不同（本机 vs 全局）；
- 452 若先做，只做**只读列表版**（worker + agent + 心跳），进度/健康等依赖 447/450 的部分后续补。

避免出现第三个 agent 状态屏。

## 6. 依赖顺序（按接口，不按 story 号）

```
446a 状态轴接线 ──┬─→ 448 Reconciler（需先定事件口径）
                  ├─→ 450 progress 历史 → 451 activity / 452 UI
447 health 解耦 ──┘（可与 446a 并行；453 在 447 的 dead 判定之后触发 457）
446b 剩余生产者（446a 之后）
449 最后写（描述新口径），且需读 Epic 78 doc 9/10 对照 adapter
```

## 7. 非目标

- 不做完整 event sourcing 回放。
- 不改 `WORKFLOW_RUN_TRANSITIONS` / `WORKFLOW_PHASE_TRANSITIONS` 图。
- 不在本变更内动 `learnings` 的表结构（那是 Epic 169 story 456 的范围）。
- 不引入新调度器组件。
