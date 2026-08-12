# Design: 命令面板接入 AgentRun 后端搜索（v6.20）

## 现状

- `AgentRun`（`agentboard/domains/scheduling/models.py`）：`agent_runs` 表，字段 `id / schedule_id / task_id / status(pending|running|success|failed|cancelled) / idempotency_key / started_at / finished_at / output / error_message / summary / log_ref / created_at`。**无 `project_id` 列**（与 Ticket 类似需 join 反查），归属项目须经 `schedule_id → AgentSchedule.project_id`。
- `AgentSchedule`（同文件）：`agent_schedules` 表，**自带 `project_id` 列**，`schedule_id` 为 `AgentRun` 必填外键（`ondelete=CASCADE`）→ join 恒有归属项目。
- `service.search_schedules`（v6.19）提供可见性收敛模板：非 admin 仅搜索 `ProjectMember` 项目，admin/None 全量；`service.search_ticket_requests`（v6.18）提供 **join 反查 project_id 附加到返回 dict** 的模板。
- `api.py` 的 `/api/search/{epics,sprints,notifications,agents,proposals,tickets,schedules}` 提供端点模板：q 必填 + limit 1-50 + `_current_user` 鉴权。
- 前端 `paletteScheduleResults`（v6.19）提供信号/分支/合并/分类标签完整模板；`models.ts` 已存在 `AgentRun` 接口（缺 `project_id` 可选字段，本变更增量补上）；项目路由 `/project/{pid}` 已支持 `schedules` section（v6.19 已扩展）。

## 设计决策

### D1: 搜索字段 = status / summary / error_message

- `AgentRun.status`：运行状态（pending/running/success/failed/cancelled，可搜「failed」「success」）；
- `AgentRun.summary`：执行摘要（Agent 回写的结果小结）；
- `AgentRun.error_message`：失败错误信息（排查场景主入口）。

匹配 `ilike('%q%')` OR 组合，与既有搜索语义一致。不搜 `output`（正文可能超长，噪音大）。

### D2: project_id 经 join AgentSchedule 反查并附加

`AgentRun` 无项目归属列，`search_runs` 采用：

```python
qry = s.query(AgentRun, AgentSchedule.project_id).join(
    AgentSchedule, AgentRun.schedule_id == AgentSchedule.id)
```

返回 `list[dict]`：`_ser(run)` 全列 + `project_id`（join 列）。前端跳转 `/project/{project_id}/schedules` 依赖该字段。

### D3: 可见性收敛 = 镜像 `search_schedules`（user_id 传入）

- `user_id=None`（内部调用）→ 全量；
- 非 admin → 仅自己 `ProjectMember` 项目下的执行记录；
- admin → 全量。

可见性过滤施加在 **join 后的 `AgentSchedule.project_id`** 上（`qry.filter(AgentSchedule.project_id.in_(member_pids))`）。API 层固定传 `uid`，端点对外永远是收敛视图。

### D4: 排序 = id desc（执行记录无 update 语义）

`AgentRun` 表无 `updated_at` 列（仅 `created_at`），排序用 `id desc`（最新执行在前），与 `list_runs` 语义一致。

## 风险与兼容性

- 路由 `/api/search/runs` 与既有 `/api/runs/{rid}`、`/api/schedules/{sid}/runs` 前缀不同，无冲突（FastAPI 精确匹配 + 前缀差异）。
- `AgentRun.status` 序列化保持原值（小写英文），前端 `runStatusLabel` 映射中文展示，不影响既有消费方。
- `models.ts` 的 `AgentRun.project_id` 为可选字段（`project_id?: number`），仅搜索端点填充，既有场景（定时计划 Tab 运行历史）不受影响。

## 参考

- `openspec/changes/palette-schedule-search-v619/design.md`（可见性收敛模板）
- `openspec/changes/palette-ticket-search-v618/design.md`（join 反查 project_id 模板）
