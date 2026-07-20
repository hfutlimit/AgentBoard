# Tasks: 状态快速筛选 chips (v2.5 / Epic 37)

## Task 862 — Epic 37: 任务列表状态快速筛选 chips（前端，high）
- [x] `filterStatus` 信号初始化读取 `localStorage['agentboard_quick_status']`
- [x] 新增 `statusCounts` computed（基于 `this.tasks()`）
- [x] 新增 `setQuickStatus(s)` 单选切换 + `persistQuickStatus()` 持久化
- [x] `clearFilters()` 联动重置 `filterStatus`；`activeFilterCount` 纳入状态筛选
- [x] 工具条新增第二个 `.task-quickfilter-bar`（全部 + 6 状态 + 色点）
- [x] 新增 `.qf-dot` 样式
- [x] `npm run build` 通过；产物 cp 至 `agentboard/web/static/`
- [x] Playwright E2E：chips 渲染、单击 active 切换、0 console/page/js-css 报错 → PASS
- [x] 创建 Epic 33 / Story 73 / Task 862，状态经合法迁移置 `in_review`
