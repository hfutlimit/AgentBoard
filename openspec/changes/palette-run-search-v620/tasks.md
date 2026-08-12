# Tasks: 命令面板接入 AgentRun 后端搜索（v6.20）

## 任务清单

### 1. 后端 service：`search_runs`
- [x] 在 `agentboard/service.py`（search_schedules 后）新增 `search_runs(s, q, limit=20, user_id=None)`
- [x] join `AgentSchedule` 反查 `project_id`（`AgentRun.schedule_id == AgentSchedule.id`）
- [x] 过滤 `status / summary / error_message` ilike OR
- [x] 可见性收敛镜像 `search_schedules`：非 admin 仅 `ProjectMember` 项目（过滤施加于 `AgentSchedule.project_id`），admin/None 全量
- [x] 排序 `id desc`，limit 截断
- [x] 返回 `list[dict]`：`_ser(run)` 全列 + 附加 `project_id`

### 2. 后端 API：`GET /api/search/runs`
- [x] `agentboard/api.py`（search_schedules_api 后）新增端点
- [x] `q` 必填（min_length=1）、`limit` 1-50、`_current_user(...)` 鉴权 + uid 收敛
- [x] 路由 `/api/search/runs` 不与既有端点冲突

### 3. 前端
- [x] `api.service.ts`：`searchRuns({q, limit})` → `GET /api/search/runs`
- [x] `models.ts`：`AgentRun` 增补可选 `project_id?: number`
- [x] `app.ts`：`paletteRunResults` 信号 + `paletteRunSearch` run 分支（hint 用 `projectName(project_id)` + `runStatusLabel(status)`）+ `paletteItems` 第 12 类合并 + open/close/短查询三处清空 + `PaletteCommand.category` 加 `'run'`
- [x] `app.html`：分类标签三元链追加 `cmd.category === 'run' ? '运行'`
- [x] `styles.css`：`.palette-item-cat.cat-run`（橙系 `#ea580c`）

### 4. 测试
- [x] `tests/test_run_search.py`：service（status/summary/error_message 匹配、join 附加 project_id、可见性 admin/成员、limit、无匹配）+ API（200、401、q 必填 422、limit 上限 422、路由不冲突、端点并存）
- [x] vitest：paletteItems 合并 / `.cat-run` 标签渲染 / open-close 清空
- [x] Playwright E2E：自包含栈（uvicorn + 静态注入 + 覆写 API URL），Ctrl+K → token → `.cat-run` → 跳转项目 `schedules` Tab → 0 报错

### 5. 验收
- [x] 单测全绿；回归无失败
- [x] MCP 状态流转：Task → in_review；Story → in_review；Epic → in_review
