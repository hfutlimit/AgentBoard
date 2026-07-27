# Tasks: 任务列表批量修改类型（v5.5）

## 实现任务
- [x] `app.ts`：新增 `taskTypes` 只读数组（task/bug/test_execution）
- [x] `app.ts`：新增 `bulkUpdateType(newType)`（循环 `updateTask`，带进度/失败兜底/刷新）
- [x] `app.ts`：`showBulkActionPanel` 类型联合新增 `'type'`
- [x] `app.html`：批量操作栏新增「批量修改类型」按钮
- [x] `app.html`：新增类型选择面板 `@if (bulkActionTarget() === 'type')`
- [x] `app.css`：新增 `.status-btn.type--{task,bug,test_execution}` 配色（含 dark）
- [x] `angular.json`：上调 `anyComponentStyle` budget 至 120kB
- [x] 构建 `npm run build` → cp `dist/frontend/browser/.` → `agentboard/web/static/`

## 验证任务
- [x] `tests/test_epic68_v55_bulk_type_e2e.py` 全绿（3 任务改 Bug、API 复核、toast、0 pageerror/console/.js+.css 404）
- [x] 回归 `pytest tests/test_epic30_cache.py` 8 passed
- [x] 回归 `tests/test_epic65_v52_bulk_duplicate_e2e.py` 全绿（bulk-action-bar 无回归）
- [x] MCP/REST 状态：Task 1123 / Story 107 / Epic 58 均置 in_review

## 追踪实体（REST 新建）
- Project 55 (AUTODEV68) → Epic 58 (v5.5) → Story 107 (68.1) → Task 1123 (high, in_review)
