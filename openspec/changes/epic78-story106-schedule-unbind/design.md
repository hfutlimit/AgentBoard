# Epic 78 · Story 106 — Design

## 数据模型（`domains/scheduling/models.py` `AgentSchedule`）

新增列（全部 nullable，旧行零迁移成本）：

| 列 | 类型 | 语义 |
|---|---|---|
| `agent` | `String(20) nullable` | 执行 Agent 名（codex/claude/workbuddy/qoder）；NULL = 继承 env `AGENTBOARD_DEFAULT_AGENT` |
| `task_id` | `FK tasks.id nullable` | 固定任务（旧单任务语义）；有值 = 每次触发跑该 task |
| `task_priority` | `String(10) nullable` | 筛选：最低优先级门槛（≥ 该优先级才 eligible） |
| `task_type` | `String(10) nullable` | 筛选：`task` / `bug` |
| `epic_id` | `FK epics.id nullable` | 筛选：仅该 Epic 下的任务 |

## 选任务逻辑（`service.pick_eligible_task`）

```python
PRIORITY_RANK = {"highest":5,"high":4,"medium":3,"low":2,"lowest":1}
ELIGIBLE_STATUSES = ("backlog", "todo")   # 未开始、可执行的活

def pick_eligible_task(s, schedule) -> Task | None:
    # 1) 固定 task_id → 直接返回（存在即返回，状态不约束——兼容旧语义）
    if schedule.task_id:
        return s.get(Task, schedule.task_id)
    # 2) 项目级：过滤
    q = s.query(Task).filter(Task.project_id == schedule.project_id,
                             Task.status.in_(ELIGIBLE_STATUSES))
    if schedule.epic_id:      q = q.filter(Task.story_id.in_(subquery of epics->stories))  # 经 story 归属
    if schedule.task_type:    q = q.filter(Task.type == schedule.task_type)
    if schedule.task_priority:
        threshold = PRIORITY_RANK[schedule.task_priority]
        q = q.filter(Task.priority.in_(ranks >= threshold))
    # 3) 排序：优先级降序 + id 升序
    return q.order_by(优先级权重 desc, Task.id.asc()).first()
```

epic 归属：Task 不直接挂 epic_id，需经 `Task.story_id → Story.epic_id` 过滤。
实现用 `Story` 子查询：`Task.story_id.in_(select(Story.id).where(Story.epic_id == schedule.epic_id))`。

## 触发流程（`scheduler._trigger_one`）

```
task_id = schedule.task_id or service.pick_eligible_task(s, schedule)
if task_id is None and schedule.task_id is None:
    log.info("schedule %d: no eligible task, skip this run")
    _advance_next_run(s, schedule)   # 幂等推进，下个周期再试
    return False
create_run(schedule_id, task_id=task_id, idempotency_key=...)
```

## 执行器（`executor.build_run_context`）

```python
agent = schedule.agent or os.environ.get("AGENTBOARD_DEFAULT_AGENT", "codex")
```

## API / MCP 契约（增量）

- `ScheduleIn` 新增：`agent`、`task_id`、`task_priority`、`task_type`、`epic_id`
- `SchedulePatch` 新增同名字段（**支持显式 null 置空**：`model_fields_set` 判断，
  `exclude_none` 只过滤未传字段，显式 null 传下去）
- `mcp_server.create_schedule` / `update_schedule` 透传
- `update_schedule` service 侧校验：`agent ∈ KNOWN_AGENTS ∪ {None}`、
  `task_priority ∈ ALL_PRIORITIES`、`task_type ∈ ALL_TYPES`、`epic_id` 存在性

## 迁移

`m0n1o2p3q4r5_agent_schedule_unbind`：`op.add_column` × 5，SQLite / MariaDB 均兼容。

## 测试

- 单测：`tests/test_schedule_unbind.py`
  - create/update 新字段校验（含显式置空）
  - `pick_eligible_task`：固定 task / 优先级门槛 / epic / type 过滤 / 空结果
  - scheduler 触发绑定：项目级自动选 task、固定 task、无 eligible 跳过
  - executor agent 读取：schedule.agent 优先，env fallback
- 前端：schedule 创建表单加 agent 下拉 + 列表显示 agent 徽标（增量，不破坏既有）
