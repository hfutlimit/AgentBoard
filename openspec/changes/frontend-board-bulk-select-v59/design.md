# Design: 看板视图批量操作（卡片多选 + 复用批量工具栏）(Epic 72 v5.9)

## 现状分析
- 列表视图：`.entity-item` 含 `.task-checkbox`（`(change)="toggleTaskSelection(item.id); $event.stopPropagation()"` + `(click)="$event.stopPropagation()"`），`.bulk-action-bar` 在 `@if (selectedTaskCount > 0)` 下渲染，且该块位于 `@if (!boardMode())` 分支**之外**，列表/看板共用。
- 键盘：`.kanban` 容器已绑定 `handleTaskKeydown`，Space 选择 / Ctrl+A 全选 / Esc 清除对看板同样生效（基于 `visibleTasks()`，已验证）。
- 批量方法：`toggleTaskSelection` / `selectAllTasks` / `clearTaskSelection` / `bulkUpdateStatus`(状态机感知 `bulkLegalStatuses`) / `bulkUpdatePriority` / `bulkUpdateType` / `bulkUpdateAssignee` / `bulkUpdateDueDate` / `bulkDuplicate` / `bulkDeleteTasks` 全部就绪。

## 方案
1. **卡片选择复选框（app.html）**
   - `<article>` 增加 `[class.selected]="selectedTasks().has(t.id)"`。
   - `.kanban-card-top` 首部新增 `<input type="checkbox" class="kanban-card-check" [checked]="selectedTasks().has(t.id)" (mousedown)/(click)="$event.stopPropagation()" (change)="toggleTaskSelection(t.id); $event.stopPropagation()">`。
   - `mousedown` + `click` 双重 stopPropagation：避免勾选时误触发卡片 `(click)="openQuickView(t)"`，也避免 mousedown 在 `draggable` 父元素上发起拖拽。

2. **选中态样式（app.css）**
   - `.kanban-card-check`：16px 复选框，brand 强调色。
   - `.kanban-card.selected`：品牌浅底 + 边框 + 1px 描边（暗色 `rgba(99,102,241,0.15)`）。

3. **复用批量工具栏**
   - 无需新增模板：勾选后 `selectedTasks().size > 0`，既有 `@if (selectedTaskCount > 0)` 的 `bulk-action-bar` 自动在看板视图上方出现，全部批量操作立即可用，状态变更保持状态机感知。

## 风险与对策
- 复选框位于 `draggable="true"` 卡片内：通过 `mousedown` stopPropagation 防止拖拽误触。
- 勾选不应打开快速查看：`click` stopPropagation 阻断卡片 `(click)` 冒泡。
- 暗色主题：`.kanban-card.selected` 增加 `[data-theme="dark"]` 适配。

## 验证策略
- Playwright E2E `tests/test_epic72_v59_board_bulk_select_e2e.py`：种子 story + 3 任务 → 看板视图勾选卡片 → 验证 `.selected`、不触发抽屉、批量工具栏计数、状态机感知批量改状态（API 复核）、Esc 清除、0 错误。
- 回归：`pytest test_epic30_cache.py` + v5.8 看板视图 E2E + 快速查看抽屉 E2E（点击开抽屉路径）。
