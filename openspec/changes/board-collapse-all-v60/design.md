# Design: 看板视图列全折叠/全展开 (v6.0)

## 现有基础设施（v5.8）
- 信号 `collapsedColumns: Set<string>`，初始从 `localStorage.agentboard_collapsed_cols` 恢复。
- `toggleColumnCollapse(status)`：单列切换并写回 localStorage。
- `isColumnCollapsed(status)`：模板判断列是否折叠（`[class.collapsed]` + 隐藏 `.kanban-col-body`）。
- 看板模板：`@for (s of statuses)` 渲染 7 列；仅看板模式（`boardMode()`）显示。

## 新增
- `readonly allColumnsCollapsed = computed(() => statuses.length>0 && collapsedColumns().size >= statuses.length)`
- `collapseAllColumns()`：将 7 个状态全部加入 `collapsedColumns` 并持久化。
- `expandAllColumns()`：清空 `collapsedColumns` 并持久化。
- 工具栏（filterbar__right，`boardToggle` 之后）新增 `@if (boardMode())` 包裹的
  `#boardColsToggle` 按钮：点击 `allColumnsCollapsed() ? expandAllColumns() : collapseAllColumns()`，
  文案随状态在「全折叠 / 全展开」间切换。
- 样式 `.btn.board-cols-toggle`（镜像 `.density-toggle`）。

## 数据流
按钮点击 → 更新 `collapsedColumns` 信号 → 模板响应式重渲染各列 `collapsed` 类 →
`@if (!isColumnCollapsed(s))` 隐藏/显示列体；持久化写入 localStorage，刷新后保留。
