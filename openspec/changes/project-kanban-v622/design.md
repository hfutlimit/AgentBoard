# 项目级看板（Epic 130）

## 问题

ticket（Story）完成后缺少可视化进度看板；agent 自动化流程（design→dev→review→qa）状态不可见，无法在一屏内掌握项目所有 Story 的阶段。

## 方案

### 1. 看板定位

- 项目级 Tab「看板」，一个项目一个看板；
- 卡片 = Story，复用现有 Story 状态流转（backlog/confirmed/todo/in_progress/in_review/verifying/done/blocked）作为列；
- 每张卡片展示其下 design/dev/qa task 的迷你状态徽章（`kb-t-design`/`kb-t-dev`/`kb-t-qa`/`kb-t-bug`）。

### 2. 进入看板标记

- `stories.in_kanban` 布尔字段（迁移 `z0a1b2c3d4e5`）；
- Story PATCH 支持 `in_kanban`；标记置 True 时 API 层联动：
  - 若 Story 仍为 backlog → 自动 `confirm_story`（backlog→confirmed，Agent 编排入口闸门）；
  - 广播 `story.confirmed` + 其下 backlog/todo 任务的 `task.available`，触发 worker 竞争认领。

### 3. 看板查询端点

`GET /api/projects/{pid}/kanban?include_all=false`：

- 默认只看 `in_kanban=True` 的 Story，按状态分桶（`columns`）+ 全量列表（`items`）；
- 每个 Story 携带 `tasks`（id/type/title/status/priority/assignee_id/estimate）；
- `include_all=true` 返回项目全部 Story（含未标记）。

### 4. 前端

- `ProjectTabKind` 增加 `'kanban'`；tab bar 新增「看板」按钮（带计数）；
- `kanbanColumns()` 按固定列序渲染；空态引导文案；
- 卡片点击跳转 Story 详情；卡片内「进入看板 / 移出看板」按钮调 PATCH；
- `toggleKanbanIncludeAll` 切换显示全部。

### 5. 并发与自动化（依赖既有能力）

- worker `_story_scan_loop` 竞争认领 confirmed Story 编排 design→dev→review→qa；
- 服务端 `confirm_story`/`claim`/`set_status` CAS 保证并发安全；
- 并发上限由 worker prefetch + CAS 认领天然约束（本迭代不引入独立配额，沿用 Ticket 全流程闸门）。

## 改动清单

- 迁移 `migrations/versions/z0a1b2c3d4e5_story_kanban_marker.py`；
- `agentboard/domains/projects/models.py`：`Story.in_kanban`；
- `agentboard/service.py`：`update_story` 支持 in_kanban + `list_project_kanban`；
- `agentboard/api.py`：`StoryPatch.in_kanban` + `GET /api/projects/{pid}/kanban` + PATCH 联动 confirm/广播；
- `frontend/src/app/{app.ts,app.html,app.css,api.service.ts,models.ts}`：看板 tab/视图/样式/API/类型。

## 验证

- `tests/test_epic130_kanban.py` 5 passed（标记读写/默认过滤/include_all/路由声明/标记联动）；
- `ng build` + vitest 53 passed；
- 迁移在 sqlite 全链验证通过。
