# Tasks: 命令面板接入 Schedule 后端搜索（v6.19）

## 任务清单

### 1. 后端 service：`search_schedules`
- [x] 在 `agentboard/service.py`（search_ticket_requests 后）新增 `search_schedules(s, q, limit=20, user_id=None)`
- [x] 过滤 `title / agent / schedule_type` ilike OR
- [x] 可见性收敛镜像 `search_proposals`：非 admin 仅 `ProjectMember` 项目，admin/None 全量
- [x] 排序 `updated_at desc, id desc`，limit 截断
- [x] 返回 `list[AgentSchedule]`（自带 project_id，`_ser` 直接可用）

### 2. 后端 API：`GET /api/search/schedules`
- [x] `agentboard/api.py`（search_tickets_api 后）新增端点
- [x] `q` 必填（min_length=1）、`limit` 1-50、`_current_user(...)` 鉴权 + uid 收敛
- [x] 路由 `/api/search/schedules` 不与既有端点冲突

### 3. 前端
- [x] `api.service.ts`：`searchSchedules({q, limit})` → `GET /api/search/schedules`
- [x] `app.ts`：`paletteScheduleResults` 信号 + `paletteRunSearch` schedule 分支（hint 用 `projectName(project_id)` + cron_expr/type + agent）+ `paletteItems` 第 11 类合并 + open/close/短查询三处清空 + `PaletteCommand.category` 加 `'schedule'` + 项目路由 section 扩展 `schedules`
- [x] `app.html`：分类标签三元链追加 `cmd.category === 'schedule' ? '计划'`
- [x] `styles.css`：`.palette-item-cat.cat-schedule`（青系 `#0284c7`）

### 4. 测试
- [x] `tests/test_schedule_search.py`：service（title/agent/type 匹配、可见性 admin/成员、limit、无匹配）+ API（200、401、q 必填 422、limit 上限 422、路由不冲突、端点并存）
- [x] vitest：paletteItems 合并 / `.cat-schedule` 标签渲染 / open-close 清空
- [x] Playwright E2E：自包含栈（uvicorn + 静态注入 + 覆写 API URL），Ctrl+K → token → `.cat-schedule` → 跳转项目 `schedules` Tab → 0 报错

### 5. 验收
- [x] 单测全绿；回归无失败
- [x] MCP 状态流转：Task → in_review；Story → in_review；Epic → in_review
- [x] 提交 + push
