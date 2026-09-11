# Design: WorkflowRun + Active Workflows Overview

> 对应 proposal.md。本文档覆盖数据模型、Event 契约、API 详细规范、前端组件、SignalR 架构、迁移。

## 1. 现状（文件:行号）

- **Story workflow 已完整**（2026-08-09 落地）：`core/application/service.py:55-87` `STORY_TRANSITIONS` + `confirm_story`（CAS `backlog→confirmed`）+ MQ `story.confirmed` → worker `handle_story`（`processors/handlers/story.py`）
- **AgentRun 现状**：`features/scheduling/models.py:36-64` `AgentRun` 表含 `schedule_id NOT NULL`（强 schedule 绑定）/ `task_id` / `agent` / `model` / `lease_worker_id` / `output` / `error_message` / `summary` / `log_ref`
- **RunEvent 现状**：`features/scheduling/models.py:67-86` `RunEvent` 表含 `actor_user_id` / `actor_username_snapshot` / `api_key_id` / `agent_registry_id` / `worker_id`（**已经有 agent 身份溯源基础**）
- **SSE 实时事件流**：`features/scheduling/router.py:195-319` `stream_run_events` 端点（replay cursor + 鉴权 + 异步生成器 + `InProcessRunEventBus`）
- **SignalR realtime 模式**：`frontend/src/app/proposal-realtime.service.ts` —— push 只带 ID + entity type，browser 收到后调 authenticated REST 拉数据（**pull truth + push notification** 模式已稳定）
- **Project Overview**：`frontend/src/app/overview-tab/overview-tab.html` —— 4 metric card + 速度图 + 焦点 Epic + 活动卡，**没有执行视图**
- **Task status_history 已有**：`service.py:task_status_history` 表，status 变化时记录，但**没有跨 task 的 phase 切分**

## 2. 数据模型

### 2.1 workflow_runs

```sql
CREATE TABLE workflow_runs (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    workflow_type VARCHAR(20) NOT NULL,
        -- v1 仅 'story'，但 schema 不写死 CHECK 约束，保留扩展性（未来 'schedule' | 'proposal' | 'ticket' | 'deployment'）
    story_id BIGINT NULL REFERENCES stories(id) ON DELETE CASCADE,
    schedule_id BIGINT NULL REFERENCES agent_schedules(id) ON DELETE SET NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'queued',
        -- 'queued' | 'running' | 'waiting' | 'blocked' | 'completed' | 'failed' | 'cancelled'
    phase VARCHAR(20) NULL,  -- 'design' | 'development' | 'qa' | NULL
    current_task_id BIGINT NULL REFERENCES tasks(id) ON DELETE SET NULL,
    reopened_from_run_id BIGINT NULL REFERENCES workflow_runs(id) ON DELETE SET NULL,
        -- Story reopen / retry terminal workflow 时关联上一条 run；老 run 历史永久不变
    started_at DATETIME NULL,
    finished_at DATETIME NULL,
    last_activity_at DATETIME NULL,
    version INT NOT NULL DEFAULT 1,  -- 乐观锁，SignalR 推送靠这个
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT ck_workflow_runs_status CHECK (status IN
        ('queued','running','waiting','blocked','completed','failed','cancelled')),
    CONSTRAINT ck_workflow_runs_phase CHECK (phase IS NULL OR phase IN
        ('design','development','qa')),
    -- workflow_type 不加 CHECK 约束，schema 可扩展，application 层 v1 仅接受 'story'
    INDEX idx_workflow_runs_project_status (project_id, status, last_activity_at DESC),
    INDEX idx_workflow_runs_story (story_id),
    INDEX idx_workflow_runs_schedule (schedule_id),
    INDEX idx_workflow_runs_reopened_from (reopened_from_run_id)
);
```

**状态机**（`service.py:WORKFLOW_RUN_TRANSITIONS`）—— **`failed` 严格终态**：

```python
WORKFLOW_RUN_TRANSITIONS = {
    "queued":   {"running", "cancelled", "failed"},
    "running":  {"waiting", "blocked", "completed", "failed", "cancelled"},
    "waiting":  {"running", "blocked", "cancelled", "failed"},
    "blocked":  {"running", "cancelled", "failed"},
    "completed": set(),  # 终态
    "failed":    set(),  # 终态（不可重试；retry 必须新建 run + reopened_from_run_id 关联）
    "cancelled": set(),  # 终态
}
```

**核心原则**：
- `failed` 是 **terminal**，**不能** `→ running` 重试。这避免了 "temporarily errored" 的语义混乱（监控 dashboard 不再可信）
- Recoverable execution failure 在 WorkflowRun 仍 `running` 时通过 **ExecutionAttempt**（= `agent_runs` 新行）解决：`WorkflowRun = running`，`AgentRun attempt 1 = failed`，`AgentRun attempt 2 = running`
- Retry exhaustion 才会把整个 WorkflowRun 推到 `failed` 终态
- Retry terminal WorkflowRun = **新建一条** WorkflowRun，通过 `reopened_from_run_id` 关联；老 run 历史永久不变

**Phase transition graph**（**显式 graph，不允许任意 mutation**）：

```python
# Service 层校验，emit phase_changed event
WORKFLOW_PHASE_TRANSITIONS = {
    "design":      {"development"},
    "development": {"qa"},
    "qa":          {"development"},  # 重工：QA 失败 → 回 development
}

def can_transition_phase(from_phase: str | None, to_phase: str) -> bool:
    if from_phase is None:
        return to_phase == "design"  # 初始 phase
    return to_phase in WORKFLOW_PHASE_TRANSITIONS.get(from_phase, set())
```

**Phase transition 规则**：
- Story v1 允许：`design → development` / `development → qa` / `qa → development`（QA 重工）
- **禁止任意 phase 回退或跳跃**（如 `design → qa` 直接跳跃不允许）
- 未来如需 `development → design`（实现发现架构问题），通过新增 `rework_requested(target_phase='design')` 显式 transition，**不**改通用规则
- 每次合法 transition emit `phase_changed` event（payload 含 `from_phase` / `to_phase` / `reason`）

### 2.2 workflow_run_events

```sql
CREATE TABLE workflow_run_events (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    workflow_run_id BIGINT NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
    event_type VARCHAR(50) NOT NULL,  -- 见 §3 契约（17 种）
    phase VARCHAR(20) NULL,
    state VARCHAR(30) NULL,
    actor_type VARCHAR(20) NOT NULL,  -- 'user' | 'agent' | 'worker' | 'system'
    actor_id BIGINT NULL,  -- user_id / agent_id / worker_id（视 actor_type 而定）
    task_id BIGINT NULL REFERENCES tasks(id) ON DELETE SET NULL,
    agent_run_id BIGINT NULL REFERENCES agent_runs(id) ON DELETE SET NULL,
    summary VARCHAR(500) NULL,  -- 一句话摘要（前端主显示用）
    payload JSON NULL,  -- 结构化详情（前端折叠区渲染用）
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT ck_workflow_events_actor CHECK (actor_type IN
        ('user','agent','worker','system')),
    INDEX idx_workflow_events_run_created (workflow_run_id, id DESC),
    INDEX idx_workflow_events_task (task_id),
    INDEX idx_workflow_events_agent_run (agent_run_id)
);
```

**关键设计点**：
- `phase` + `state` 两字段**解耦** —— 避免 `design_in_review` / `qa_changes_requested` 这样的 enum 爆炸
- `summary` 是给 UI 主显示用的一句话（"Codex started implementation"），`payload` 是结构化详情
- `id DESC` 索引天然支持"最新事件优先"查询（before_id 翻页）

### 2.3 agent_runs 改造（slice 3）

```sql
-- 新增列
ALTER TABLE agent_runs ADD COLUMN workflow_run_id BIGINT NULL REFERENCES workflow_runs(id) ON DELETE SET NULL;
ALTER TABLE agent_runs ADD COLUMN stage_type VARCHAR(20) NULL;  -- 'design' | 'dev' | 'review' | 'qa'

-- 改 schedule_id 为 nullable
ALTER TABLE agent_runs MODIFY COLUMN schedule_id BIGINT NULL;  -- 原 NOT NULL

-- 新增索引
CREATE INDEX idx_agent_runs_workflow_run ON agent_runs(workflow_run_id);
```

**旧行迁移**：
- `schedule_id` 保留原值（即使为 NULL 也不动）
- `workflow_run_id` 全 NULL
- `stage_type` 全 NULL
- 双数据库（Alembic）：MariaDB 与 SQLite 两条 SQL 都要测试

**`create_agent_run` 抽象**：

```python
def create_agent_run(
    s: Session,
    *,
    trigger_type: str,  # 'schedule' | 'workflow'
    workflow_run_id: int | None = None,
    schedule_id: int | None = None,
    task_id: int | None = None,
    agent_id: int | None = None,
    stage_type: str | None = None,  # 'design' | 'dev' | 'review' | 'qa'
    model: str | None = None,
    idempotency_key: str | None = None,
) -> AgentRun:
    # 校验：trigger_type=workflow → workflow_run_id 必填；trigger_type=schedule → schedule_id 必填
    ...
```

旧 `create_run(schedule_id=, task_id=, ...)` 内部转调 `create_agent_run(trigger_type='schedule', schedule_id=, ...)`，签名兼容。

## 3. Event 契约（17 种完整粒度）

| event_type | 何时 emit | phase | state | actor_type | 关键 payload |
|---|---|---|---|---|---|
| `workflow_started` | 创建 WorkflowRun 时 | 初始 phase | `running` | `user` 或 `system` | `{workflow_type, story_id, initial_phase, reopened_from_run_id?}` |
| `phase_changed` | phase 切换时（按 transition graph 校验） | 新 phase | `running` | `system` | `{from_phase, to_phase, reason}` |
| `workflow_reopened` | 跨 run 生命周期：Story reopen / retry terminal workflow 时**新建**一条 run | 初始 phase | `running` | `user` 或 `system` | `{new_run_id, reopened_from_run_id, reason: "story_reopened" \| "user_retry"}` |
| `task_created` | 任务首次出现 | 当时 phase | `queued` | `system` 或 `user` | `{task_id, task_type, title}` |
| `task_assigned` | agent 认领 / 调度 | 当时 phase | `running` | `agent` 或 `system` | `{task_id, agent, assignment_id}` |
| `task_started` | agent 真正开始执行 | 当时 phase | `running` | `agent` | `{task_id, agent_run_id, model, started_files}` |
| `task_submitted` | agent 提交评审 | 当时 phase | `in_review` | `agent` | `{task_id, summary, commit_ref, inspected_files[]}` |
| `review_requested` | 服务端生成评审 | 当时 phase | `reviewing` | `system` | `{task_id, reviewer, review_mode, quorum}` |
| `review_completed` | 评审人给出结论 | 当时 phase | `done` | `agent` | `{task_id, verdict, findings[], duration_seconds}` |
| `changes_requested` | 评审打回 | 当时 phase | `changes_requested` | `agent` | `{task_id, reviewer, findings_count, summary}` |
| `task_reopened` | **单 run 内**：review 打回导致 task done → in_progress（同 run 内事件循环，**不**创建新 run） | 当时 phase | `reopened` | `user` 或 `agent` | `{task_id, reason, from_status}` |
| `workflow_completed` | 所有 task done | final phase | `completed` | `system` | `{summary, duration_seconds, task_count}` |
| `workflow_failed` | 致命失败 | last phase | `failed` | `system` | `{reason, failed_at_stage, last_error}` |
| `workflow_cancelled` | 用户取消 | 当时 phase | `cancelled` | `user` | `{cancelled_by, reason}` |
| `blocked` | 进入阻塞 | 当时 phase | `blocked` | `system` 或 `user` | `{reason, blocking_dependencies[]}` |
| `unblocked` | 解除阻塞 | 当时 phase | `running` | `system` 或 `user` | `{reason}` |
| `retry_scheduled` | 触发 retry attempt（仅在 `WorkflowRun` 仍 `running` 时；retry 失败 1 次 agent 进程重启等） | 当时 phase | `retrying` | `system` | `{retry_kind, attempt, reason, last_error, target_agent_run_id?}` |
| `agent_heartbeat_lost` | CLI 探测失败 | 当时 phase | `waiting` | `worker` | `{agent_id, last_seen_at, probe_message}` |
| `worker_lease_expired` | lease 超时未续 | 当时 phase | `waiting` | `system` | `{worker_id, lease_expires_at, owner_run_id}` |

**`task_reopened` vs `workflow_reopened` 严格区分**：

| 触发 | event | 副作用 |
|---|---|---|
| 单 run 内 review 打回 → task 重新进入 in_progress | `task_reopened` | **不**创建新 run |
| Story reopen / retry terminal workflow | `workflow_reopened` | **新建**一条 WorkflowRun + `reopened_from_run_id` 关联；老 run 历史永久不变 |

**`retry_kind` 枚举**（`retry_scheduled` event 的 payload 字段，**仅 event/diagnostic metadata，**不是统一 workflow retry counter**）：
- `execution`：Agent 执行重试（如 CLI 退出码非 0、AgentRuntime 异常）
- `review_cycle`：Reviewer 打回 → developer 修复的循环（task 维度）
- `mq_delivery`：RabbitMQ 投递重试（**普通用户 UI 不可见**，仅 diagnostics / 详情折叠区可见）

UI 展示原则：
- `execution` 和 `review_cycle` 分桶独立计数（**不**共用 `retry_count`）
- `mq_delivery` 不进 Active Workflows 卡片 / timeline 主显示，仅在 workflow detail 的 diagnostics 折叠区可见

**emit helper**：

```python
# service.py
def emit_workflow_event(
    s: Session,
    *,
    workflow_run_id: int,
    event_type: str,
    actor_type: str,
    actor_id: int | None = None,
    phase: str | None = None,
    state: str | None = None,
    task_id: int | None = None,
    agent_run_id: int | None = None,
    summary: str | None = None,
    payload: dict | None = None,
) -> WorkflowRunEvent:
    # 1. 校验 event_type 在白名单
    # 2. 写库
    # 3. 更新 workflow_runs.last_activity_at = now() + version++
    # 4. SignalR broadcast workflow.changed {project_id, workflow_run_id, version}
    # （SignalR 广播在 slice 6 接入；slice 2 先只写库）
```

## 4. API 详细规范

### 4.1 `GET /api/projects/{project_id}/workflow-runs`

**Query**：
- `status=active`（默认）→ 仅返回 `running` / `waiting` / `blocked`
- `status=all` → 全部
- `workflow_type=story`（可选过滤）
- `limit=20`（默认，最大 100）
- `offset=0`

**Response**（200）—— **必须内嵌完整摘要，避免 N+1**：

```json
{
  "items": [
    {
      "id": 733,
      "project_id": 12,
      "workflow_type": "story",
      "story_id": 434,
      "story_title": "Add Review Cycle",
      "status": "running",
      "phase": "development",
      "current_task_id": 822,
      "reopened_from_run_id": null,
      "started_at": "2026-09-11T10:31:00Z",
      "last_activity_at": "2026-09-11T10:49:00Z",
      "elapsed_seconds": 1080,
      "version": 27,

      "active_executions": [
        {
          "agent_run_id": 105,
          "task_id": 822,
          "task_title": "Backend review state machine",
          "agent": "codex",
          "model": "gpt-5",
          "stage_type": "dev",
          "status": "running",
          "started_at": "2026-09-11T10:47:00Z",
          "elapsed_seconds": 120,
          "summary": "Implementing review state machine"
        },
        {
          "agent_run_id": 106,
          "task_id": 823,
          "task_title": "Frontend review panel",
          "agent": "minimax",
          "model": "MiniMax-M2",
          "stage_type": "dev",
          "status": "running",
          "started_at": "2026-09-11T10:49:00Z",
          "elapsed_seconds": 60,
          "summary": "Implementing review UI"
        }
      ],

      "latest_event": {
        "id": 1523,
        "event_type": "task_started",
        "phase": "development",
        "state": "running",
        "actor_type": "agent",
        "actor_name": "codex",
        "task_id": 822,
        "agent_run_id": 105,
        "summary": "Codex started implementation",
        "created_at": "2026-09-11T10:49:00Z"
      }
    }
  ],
  "total": 1,
  "next_offset": null
}
```

**核心原则**：Angular 一次请求就画完 Overview，**不**再为每条 run 发 GET run/123 + GET events/123 + GET executions/123。性能预算：项目 1000 story 下 < 200ms。

**实现要点**：
- 单 SQL 拉 `workflow_runs` 主表（索引 `(project_id, status, last_activity_at DESC)`）
- 用 `ANY(SELECT workflow_run_id FROM ...)` 或 LATERAL JOIN 批量拉 `latest_event`（避免 N+1）
- 用 `ANY(SELECT workflow_run_id FROM agent_runs WHERE status='running' ...)` 批量拉 `active_executions`（避免 N+1）
- 集成测试 `test_active_workflows_no_n_plus_1.py` 断言：1000 story 项目 list 端点 DB 查询次数 ≤ 3（主表 + events + agent_runs）

### 4.2 `GET /api/workflow-runs/{run_id}`

**Response**（200）：

```json
{
  "id": 733,
  "project_id": 12,
  "workflow_type": "story",
  "story_id": 434,
  "story_title": "Add Review Cycle",
  "status": "running",
  "phase": "development",
  "reopened_from_run_id": null,
  "stages": [
    {"type": "design", "status": "completed", "started_at": "...", "finished_at": "..."},
    {"type": "development", "status": "running", "started_at": "...", "finished_at": null},
    {"type": "qa", "status": "pending", "started_at": null, "finished_at": null}
  ],
  "active_executions": [
    {
      "agent_run_id": 105,
      "task_id": 822,
      "task_title": "Backend review state machine",
      "agent": "codex",
      "model": "gpt-5",
      "stage_type": "dev",
      "status": "running",
      "started_at": "2026-09-11T10:47:00Z",
      "elapsed_seconds": 120,
      "summary": "Implementing review state machine"
    }
  ],
  "started_at": "2026-09-11T10:31:00Z",
  "last_activity_at": "2026-09-11T10:49:00Z",
  "finished_at": null,
  "version": 27
}
```

### 4.3 `GET /api/workflow-runs/{run_id}/events`

**Query**：
- `before_id=`（keyset paging）
- `limit=50`（默认，最大 200）

**Response**（200）：

```json
{
  "items": [
    {
      "id": 1523,
      "event_type": "task_started",
      "phase": "development",
      "state": "running",
      "actor_type": "agent",
      "actor_id": 12,
      "actor_name": "codex",
      "task_id": 822,
      "agent_run_id": 105,
      "summary": "Codex started implementation",
      "payload": {"inspected_files": ["src/review/state.py"]},
      "created_at": "2026-09-11T10:49:00Z"
    }
  ],
  "next_before_id": 1423
}
```

### 4.4 `GET /api/workflow-runs/{run_id}/executions`

**Response**（200）：

```json
{
  "items": [
    {
      "agent_run_id": 101,
      "task_id": 820,
      "task_title": "Design review state machine",
      "agent": "claude",
      "model": "claude-4.5",
      "stage_type": "design",
      "status": "success",
      "started_at": "2026-09-11T10:32:00Z",
      "finished_at": "2026-09-11T10:37:00Z",
      "summary": "Design submitted",
      "error_message": null
    }
  ]
}
```

## 5. 前端组件

### 5.1 ActiveWorkflowsCard（Overview 新增）

**位置**：`overview-tab.html` velocity chart 下方，全宽卡片

**结构**：
```html
<section class="overview-active-workflows-card workspace-card">
  <header>
    <h4>Active Workflows</h4>
    <span class="badge">{{ activeWorkflows.length }} running</span>
  </header>

  @for (w of activeWorkflows; track w.id) {
    <article class="workflow-row clickable" (click)="openWorkflow(w.id)">
      <div class="workflow-head">
        <strong>Story #{{ w.story_id }} — {{ w.story_title }}</strong>
        <span class="workflow-elapsed">{{ formatElapsed(w.elapsed_seconds) }}</span>
      </div>

      <!-- 阶段 rail（5 态语义，不显示百分比 progress bar） -->
      <ol class="workflow-rail">
        @for (stage of w.stages; track stage.type) {
          <li [class]="'stage-' + stage.status">
            <span class="stage-mark">
              @switch (stage.status) {
                @case ('completed') { ✓ }
                @case ('active')    { ● }
                @case ('pending')   { ○ }
                @case ('blocked')   { ⚠ }
                @case ('failed')    { × }
              }
            </span>
            <span class="stage-name">{{ stageLabels[stage.type] }}</span>
          </li>
        }
      </ol>

      @if (w.reopened_from_run_id) {
        <div class="workflow-reopened-hint">
          Reopened from Run #{{ w.reopened_from_run_id }}
        </div>
      }

      <div class="workflow-executions">
        @for (exec of w.active_executions; track exec.agent_run_id) {
          <div class="exec-row">
            <strong>{{ exec.agent }}</strong>
            <span>{{ exec.summary }}</span>
            <span class="exec-elapsed">{{ formatElapsed(exec.elapsed_seconds) }}</span>
          </div>
        }
      </div>

      <div class="workflow-latest">
        Latest: {{ w.latest_event.summary }} · {{ timeAgo(w.latest_event.created_at) }}
      </div>

      <button class="link-btn">Details →</button>
    </article>
  }
</section>
```

**无 active workflow 时**：
```html
<div class="overview-empty">
  No active workflows. Confirm a Story to start one.
</div>
```

### 5.2 WorkflowDetailPage（新页面，route = `workflow-runs/:id`）

**结构**：
```html
<div class="workflow-detail">
  <header>
    <h2>Story #{{ story_id }} — {{ story_title }}</h2>
    <span class="status-badge status-{{ status }}">{{ status }}</span>
    <span class="phase-badge phase-{{ phase }}">{{ phaseLabels[phase] }}</span>
    <span class="elapsed">{{ formatElapsed(elapsed) }}</span>
  </header>

  <section class="workflow-rail-detail">
    @for (stage of stages; track stage.type) { ... }
  </section>

  <section class="workflow-current">
    <h3>Current</h3>
    @for (exec of active_executions; track exec.agent_run_id) {
      <div class="exec-detail">
        <strong>{{ exec.agent }} — {{ exec.task_title }}</strong>
        <span class="exec-state">{{ exec.status }}</span>
        <span>{{ formatElapsed(exec.elapsed_seconds) }}</span>
        <details>
          <summary>View execution details</summary>
          <pre>{{ exec.payload | json }}</pre>
        </details>
      </div>
    }
  </section>

  <section class="workflow-timeline">
    <h3>Timeline</h3>
    <ol>
      @for (e of events; track e.id) {
        <li class="timeline-event event-{{ e.event_type }}">
          <time>{{ formatTime(e.created_at) }}</time>
          <span class="event-icon">{{ eventIcons[e.event_type] }}</span>
          <strong>{{ e.actor_name || e.actor_type }}</strong>
          <span>{{ e.summary }}</span>
          @if (e.payload) {
            <details>
              <summary>Details</summary>
              <pre>{{ e.payload | json }}</pre>
            </details>
          }
        </li>
      }
    </ol>
  </section>
</div>
```

**关键设计**：
- 默认不展开 `payload`（避免 Jenkins 风大日志）
- 每个 event 折叠区 + agent output 折叠区都按需展开
- Timeline 默认显示最近 50 条，"Load more" 走 before_id 翻页

## 6. SignalR 架构

### 6.1 后端 broadcast

**新 event**：`workflow.changed`

```python
# realtime.py
async def broadcast_workflow_changed(workflow_run_id: int, version: int):
    run = get_workflow_run(workflow_run_id)
    await signalr_broadcast(
        group=f"project:{run.project_id}",
        event="workflow.changed",
        payload={
            "project_id": run.project_id,
            "workflow_run_id": workflow_run_id,
            "version": version,
        }
    )
```

**触发点**：每次 `emit_workflow_event` 写库后 + 每次 `workflow_runs` 状态/phase 变化后

### 6.2 前端订阅

**复用**：`proposal-realtime.service.ts` 的 SignalR connection

```typescript
// workflow-realtime.service.ts（新建）
export class WorkflowRealtimeService {
  private signalr = inject(ProposalRealtimeService);  // 复用连接
  private api = inject(ApiService);

  watchProject(projectId: number, onChanged: (workflowRunId: number) => void) {
    this.signalr.joinGroup(`project:${projectId}`);

    return this.signalr.on('workflow.changed', (payload) => {
      if (payload.project_id === projectId) {
        onChanged(payload.workflow_run_id);
      }
    });
  }
}
```

**Angular 组件**：
```typescript
// active-workflows-card.component.ts
ngOnInit() {
  this.workflowRealtime.watchProject(this.projectId, (workflowRunId) => {
    this.refreshOne(workflowRunId);  // 单独刷新这一条，不全量拉
  });
}
```

**核心原则**：
- push 只带 ID + version，不带 payload
- 浏览器收到 → 调 authenticated REST 拉详情
- REST 返回时校验 `version >= push.version`，否则忽略（防乱序）

## 7. 兼容性 / 迁移

### 7.1 Schema 迁移

Alembic 双后端迁移（MariaDB + SQLite），每个 slice 一次 migration：

| Slice | Migration |
|---|---|
| 1 | `m5n6o7p8q9r0_create_workflow_runs_and_events.py` |
| 3 | `s7t8u9v0w1x2_agent_runs_workflow_nullable.py` |

`workflow_type` schema **不**加 CHECK 约束（v1 实际只写 `story`，但 `schedule` / `proposal` / `ticket` / `deployment` 字段允许——**schema 可扩展，实现只支持 story**）。

### 7.2 API 兼容

- **新增**：4 个端点（active-workflows / detail / events / executions）；**不加** cancel / pause / resume / retry 端点（v1 走原 Story `set_status` 即可，避免 premature execution control API）
- **不变**：
  - 旧 `POST /api/schedules/{sid}/runs`（内部转 `create_agent_run`）
  - 旧 `GET /api/schedules/{sid}/runs`（仅返回 schedule_id 绑定的 run）
  - 旧 `GET /api/agent-runs/{rid}/events/stream`（保留 schedule 触发 run 的 SSE 流）

### 7.3 MCP 兼容

**新增 5 个 MCP 工具**（slice 2-3 接入）：
- `workflow_run_get(workflow_run_id)`
- `workflow_run_list_events(workflow_run_id, before_id=, limit=)`
- `workflow_run_emit_event(workflow_run_id, event_type, summary, payload=)` —— agent 主动写事件
- `agent_run_create(workflow_run_id, task_id, agent_id, stage_type=)` —— 抽象创建
- `agent_run_set_stage(agent_run_id, stage_type)` —— 标识角色

**旧工具不变**：`create_run` / `list_runs` / `report_run_result` 全部保留。

### 7.4 前端兼容

- Overview 新增卡片，**不改老卡片**
- 新增 route `/workflow-runs/:id`，不破坏老路由
- `proposal-realtime.service.ts` 是 shared service，**复用**而非新建
- 阶段 rail 视觉状态：5 态（`completed` / `active` / `pending` / `blocked` / `failed`），**不**显示百分比 progress bar

## 8. 测试计划

### 8.1 单元测试

| 文件 | 覆盖 |
|---|---|
| `tests/test_workflow_run_state_machine.py` | 状态迁移合法表 / blocked 全向可达 / 终态不可再迁移 / **`failed` 严格终态**（不能 `→ running`）/ 取消不可重试 |
| `tests/test_workflow_event_contract.py` | 17 种 event_type 枚举校验 / 必填字段 / payload schema / phase+state 解耦 / **`task_reopened` vs `workflow_reopened` 严格区分** |
| `tests/test_agent_run_nullable_migration.py` | schedule_id nullable / 老行保留原值 / 新行可写 NULL / workflow_run_id FK 校验 |
| `tests/test_workflow_run_phase_transition_graph.py` | 显式 transition graph 校验：`design→development` / `development→qa` / `qa→development` 合法；`design→qa` / 任意 phase 回退非法 |

### 8.2 集成测试

| 文件 | 覆盖 |
|---|---|
| `tests/test_story_to_workflow_run.py` | confirm_story → WorkflowRun 创建 → 初始 phase=design / 初始 event=workflow_started |
| `tests/test_task_to_workflow_event.py` | claim → task_assigned / start → task_started / submit → task_submitted / review → review_completed / done → workflow 推进 |
| `tests/test_workflow_run_phase_rework.py` | `qa→development` 重工合法，发 `phase_changed` event |
| `tests/test_retry_three_kinds.py` | 3 种 retry 各自计数独立（MQ delivery 不可见 / execution / review_cycle） |
| `tests/test_workflow_run_reopen.py` | Story reopen → 新 WorkflowRun + `reopened_from_run_id` 关联 + 旧 run 状态不变 + `workflow_reopened` event 落库 |
| `tests/test_workflow_run_failed_terminal.py` | `failed` 终态：不能 `→ running` / retry 只能新建 run |
| `tests/test_active_workflows_no_n_plus_1.py` | list 端点一次返回所有 summary，**不**触发 per-run 子查询（断言 DTO 完整 + DB 查询次数 ≤ 3） |
| `tests/test_workflow_run_block_unblock.py` | blocked → unblocked 状态机 + 事件流 |
| `tests/test_workflow_run_concurrent_safety.py` | 并发 emit event 时 version 乐观锁不冲突 |

### 8.3 E2E（tests/e2e/）

| 文件 | 覆盖 |
|---|---|
| `test_e2e_active_workflows_panel.py` | 项目 Overview 看到 Active Workflows 卡片（Playwright 截图 + 元素断言） |
| `test_e2e_workflow_detail_timeline.py` | 详情页 timeline 完整（17 种 event 至少出现 5 种） |
| `test_e2e_workflow_signalr_push.py` | SignalR workflow.changed → REST refresh 全链路（用 Playwright 监听 network） |
| `test_e2e_workflow_phase_progression_e2e.py` | 完整 Story：confirmed → design → development → qa → completed 17 event 全链路 |

### 8.4 性能

| 指标 | 目标 | 测法 |
|---|---|---|
| active-workflows 查询（1000 story 项目） | < 200ms | `pytest-benchmark` |
| workflow_run_events 列表（200 条/页） | < 100ms | `pytest-benchmark` |
| SignalR push 延迟 | < 500ms | E2E 计时 |

### 8.5 dod_registry

按项目约定，每 slice 完成后追加到 `tests/e2e/dod_registry.py` + 更新 `docs/e2e-plan.md` section 14 进度表 + 更新 README Status block，然后 commit + push。

**README Status 注意事项**：UI 真正出来前（slice 1-4）只标 "Workflow Run foundation" 而非 "Overview implemented"，避免假绿。Slice 5（Angular panel + detail timeline）完成后才升级为 "Active Workflows Overview 实施中"。

## 9. 风险与回退

| 风险 | 缓解 |
|---|---|
| Schema 迁移影响大表（agent_runs） | 双 SQL 路径测试 / 迁移前回滚脚本 / staging 先跑 |
| SignalR 推送频率过高 | `version` 乐观锁去重 + 客户端 100ms debounce |
| 17 种 event 契约过细 | `payload` 用 json，前端可选择性消费，server 不强制 schema |
| Story workflow 重入（done 退回 in_progress） | **不**复用旧 WorkflowRun；**新建**一条 + emit `workflow_reopened` event + `reopened_from_run_id` 关联；老 run 状态永久不变 |
| `failed` terminal 误用 | terminal 集（`completed` / `failed` / `cancelled`）显式约束；retry 只能新建 run；监控 dashboard 完全可信（`failed` 不会再变 `running`） |
| Phase 任意 mutation | 显式 transition graph 校验 + 集成测试覆盖非法路径 + 未来新 transition 必须新增 `rework_requested` 显式入口 |
| active-workflows N+1 | list 端点 DTO 内嵌 latest_event + active_executions；集成测试断言 DB 查询次数 ≤ 3 |
| 双数据库迁移差异 | Alembic 双后端实测 + CI 跑 SQLite + MariaDB 两套 |
