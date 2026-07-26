# Tasks: 批量指派面板成员头像/姓名选择器 + 搜索（v5.1）

## Epic 54（id=54）：前端体验升级 v5.1 — 批量指派面板增强
## Story 103（id=103）：Story 64.1 批量指派面板成员头像与搜索
## Task 1119（id=1119，high）：Task: 批量指派面板成员头像与搜索增强

### 实现任务
- [x] app.ts：新增 `bulkAssignSearch` 信号与 `filteredBulkMembers()` 方法（按 username 过滤 `members()`）
- [x] app.ts：`showBulkActionPanel('assignee')` / `closeBulkActionPanel()` 重置 `bulkAssignSearch`
- [x] app.html：`assignee` 批量面板由 `<select>` 替换为搜索框 + 成员 chip 列表（含「未指派」），点击即应用/清除
- [x] app.css：删除 `.bulk-assignee-select`，新增 `.bulk-assign-search` / `.bulk-panel--assignee` / `.bulk-member-list` / `.bulk-member-chip`（含 active/hover）/ `.bulk-member-empty`

### 验证任务
- [x] 构建：`npm run build`（managed node 22.22.2，清 `.angular/cache`）→ 新产物 `main-WEVKENIO.js` cp 至 `agentboard/web/static/`
- [x] E2E `tests/test_epic64_v51_bulk_assign_picker_e2e.py`：成员 chip+头像渲染 / 搜索过滤 / 点击 chip 指派 / 点击未指派清除 / API 复核 / 0 错误 — ALL PASS
- [x] 回归：后端 `pytest test_epic30_cache.py`（8 passed）+ 前端 v5.0 详情 Markdown、v4.5 抽屉导航 E2E 全绿

### 状态
- Task 1119：in_review（backlog→todo→in_progress→in_review 合法链）
- Story 103：in_review
- Epic 54：in_review
