# Change: 任务列表批量修改类型（type）（v5.5）

## Why
任务列表的批量操作栏（bulk-action-bar）已具备「批量修改状态 / 优先级 / 批量指派 / 批量改截止日期 / 批量复制 / 批量删除」六类操作，但**缺少「批量修改类型」**。类型为任务的核心属性（task / bug / test_execution），在统一将一批任务从「任务」转为「Bug」等场景下，用户仍需逐条进入详情页修改，效率低。补齐该操作可让 bulk 家族完整覆盖任务全部可批量字段。

## What Changes
- `frontend/src/app/app.ts`：
  - 新增 `readonly taskTypes: string[] = ['task', 'bug', 'test_execution']`（与分组/类型筛选枚举一致）。
  - 新增 `bulkUpdateType(newType: string)`：复用 `bulkDuplicate` 的「逐任务 `api.updateTask(id, {type})` 循环」模式，带 `bulkProgress` 进度、失败兜底与 `refresh()` 局部刷新，**零后端契约变更**。
  - `showBulkActionPanel` 类型联合新增 `'type'`。
- `frontend/src/app/app.html`：
  - 批量操作栏新增「批量修改类型」按钮 → 打开类型选择面板（`.bulk-panel`）。
  - 新增 `@if (bulkActionTarget() === 'type')` 面板，遍历 `taskTypes` 渲染 `.status-btn.type--{t}` 按钮，调用 `bulkUpdateType(t)`。
- `frontend/src/app/app.css`：新增 `.status-btn.type--task/bug/test_execution` 三档配色（含 dark 主题），复用 `.status-btn` 基础样式。
- `angular.json`：`anyComponentStyle` budget 由 80kB 上调至 120kB（app.css 累计体积超限，历史同类调整）。

## Impact
- 纯前端，零后端契约变更（复用既有 `PATCH /api/tasks/{id}` 单任务更新端点）。
- 不改动 TaskIn/TaskPatch 等契约，不影响 MCP/API 其他调用方。
- 新增 E2E `tests/test_epic68_v55_bulk_type_e2e.py` 覆盖「勾选→批量改类型→API 复核→0 错误」不变量。

## Status
Implemented（in_review）
