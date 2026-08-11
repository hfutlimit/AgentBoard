# Design: 命令面板接入 Schedule 后端搜索（v6.19）

## 现状

- `AgentSchedule`（`agentboard/domains/scheduling/models.py`）：`agent_schedules` 表，字段 `id / project_id / title / schedule_type(once|cron) / cron_expr / agent / task_id / task_priority / task_type / epic_id / enabled / next_run_at / last_run_at / created_at / updated_at`。**自带 `project_id` 列**（与 Ticket 需反查不同），`_ser(AgentSchedule)` 直接可用。
- `agent` 字段为枚举（codex/claude/workbuddy/qoder），`schedule_type` 为 once/cron —— 两者均可作为搜索关键词（用户搜「codex」「once」可定位计划）。
- `service.search_proposals`（v6.17）提供可见性收敛模板：非 admin 仅搜索 `ProjectMember` 项目，admin/None 全量。
- `api.py` 的 `/api/search/{epics,sprints,notifications,agents,proposals,tickets}` 提供端点模板：q 必填 + limit 1-50 + `_current_user` 鉴权。
- 前端 `paletteTicketResults`（v6.18）提供信号/分支/合并/分类标签完整模板；`models.ts` 已存在 `AgentSchedule` 接口（字段与 `_ser(AgentSchedule)` 完全对齐）。项目路由 `/project/{pid}` 已支持 `section` 解析（proposals/documents），需扩展 `schedules`。

## 设计决策

### D1: 搜索字段 = title / agent / schedule_type

- `AgentSchedule.title`：计划标题（主搜索字段）；
- `AgentSchedule.agent`：绑定 Agent 标识（枚举，可搜「codex」「workbuddy」）；
- `AgentSchedule.schedule_type`：计划类型（once/cron）。

匹配 `ilike('%q%')` OR 组合，与既有搜索语义一致。

### D2: 可见性收敛 = 镜像 `search_proposals`（user_id 传入）

计划归属于项目，可见性必须与项目成员一致：

- `user_id=None`（内部调用）→ 全量；
- 非 admin → 仅自己 `ProjectMember` 项目下的计划；
- admin → 全量。

API 层固定传 `uid`（`_current_user(...).id`），端点对外永远是收敛视图。

### D3: 返回结构 = `_ser(AgentSchedule)` 全列

与 Ticket（反查 project_id）不同，`AgentSchedule` 自带 `project_id`，API 层 `[service._ser(x) for x in rows]` 直接返回即可，前端 `projectName(sch.project_id)` 显示项目名。

### D4: 前端跳转 = `/project/{pid}/schedules`

项目路由解析（`app.ts` `parseRoute` 附近）现有 `section` 三态（proposals/documents/缺省 epics），扩展为四态加 `schedules` → `activeTab.set('schedules')`。`loadProjectTab('schedules')` 分支已存在（`listSchedules` + `schedules.set`），零新增加载逻辑。

### D5: 分类标签与色系

- `app.html` 分类三元链追加 `cmd.category === 'schedule' ? '计划'`；
- `styles.css` 新增 `.palette-item-cat.cat-schedule`（青系 `#0284c7`，与 task 蓝、project 紫、story 青蓝错开）；
- `PaletteCommand.category` 联合类型追加 `'schedule'`。

## 风险与缓解

- **路由冲突**：`/api/search/schedules` 与 `/api/projects/{pid}/schedules` 前缀不同（search 非数字 pid），FastAPI 精确匹配优先，无冲突；测试覆盖。
- **AgentSchedule 枚举**：`agent`/`schedule_type` 为校验枚举，测试种子用合法值（codex/claude/workbuddy + once/cron）。
