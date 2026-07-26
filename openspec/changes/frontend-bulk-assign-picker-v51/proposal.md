# Change: 批量指派面板增强 — 成员头像/姓名选择器 + 搜索（v5.1）

## Why
v3.0 引入的批量指派能力在任务列表批量操作区使用原生 `<select>` 下拉选择指派人，与 v3.8「行内快速指派」菜单（头像 + 姓名 chip）的体验割裂；当项目成员较多时，`<select>` 无法搜索定位，操作效率低。为统一交互语言并提升大团队的批量指派可用性，将批量指派面板升级为与行内改指派一致的「头像 + 姓名」chip 选择器，并支持按用户名搜索过滤。

## What Changes
- `frontend/src/app/app.ts`：
  - 新增 `bulkAssignSearch` 信号（成员搜索关键字）与 `filteredBulkMembers()` 方法（按 `username` 过滤 `members()`）。
  - `showBulkActionPanel('assignee')` / `closeBulkActionPanel()` 时重置 `bulkAssignSearch`，避免搜索词跨面板残留。
- `frontend/src/app/app.html`：
  - `bulk-action-bar` 中 `assignee` 面板由 `<select class="bulk-assignee-select">` 替换为：搜索输入框 `.bulk-assign-search` + 可滚动成员列表 `.bulk-member-list`（「未指派」chip + 每个成员一个 `.bulk-member-chip`，含头像 `.assignee-avatar-sm` 与姓名），点击 chip 即时批量指派 / 清除。
- `frontend/src/app/app.css`：
  - 删除不再使用的 `.bulk-assignee-select`，新增 `.bulk-assign-search` / `.bulk-panel--assignee` / `.bulk-member-list` / `.bulk-member-chip`（含 `.active` 高亮、hover 微交互）/ `.bulk-member-empty` 样式（复用既有的 `--primary` / `--border` / `--text` 主题变量，dark 自适应）。
- 纯前端，零后端契约变更（复用既有 `bulkUpdateAssignee()` 与 `getAssigneeName` / `getAssigneeInitials`）。

## Impact
- 仅前端模板/样式/局部方法变更，不影响任何 API 契约或后端逻辑。
- 交互与 v3.8 行内改指派菜单一致，降低认知负担；搜索过滤适配大成员列表。
- 新增 E2E `tests/test_epic64_v51_bulk_assign_picker_e2e.py` 覆盖：成员 chip + 头像渲染、搜索过滤（命中 1 / 无匹配提示）、点击 chip 即时指派、点击「未指派」即时清除、API 复核 assignee_id、0 控制台/页面/404 错误。

## Status
Implemented（in_review）
