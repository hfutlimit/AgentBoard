# Design: 任务列表看板视图渲染 (Epic 71 v5.8)

## 渲染结构
`app.html` 任务列表区由 `@if (!boardMode()) { 列表 } @else { 看板 }` 二选一：
- 列表分支保持原样（字节级不变）。
- 看板分支：`@for (s of statuses)` 渲染 7 个 `.kanban-col`，每列含
  - `.kanban-col-header`：状态色点 + 状态名 + 计数徽章（`getStatusTaskCount`）+ 折叠箭头（`isColumnCollapsed`/`toggleColumnCollapse`）。
  - `.kanban-col-body`：拖拽目标（`onKanbanDragOver/Leave/Drop`），`@for (t of tasksForStatus(s))` 渲染 `.kanban-card`。

## 卡片
复用既有处理链：
- `draggable="true"` + `(dragstart)="onKanbanDragStart($event,t)"` / `(dragend)="onKanbanDragEnd()"`。
- `(click)="openQuickView(t)"` 打开快速查看抽屉（与列表一致）。
- 角标 `.task-quick-complete.kanban-qc` 调 `toggleTaskComplete`（绝对定位，复用 A-22/B-22 样式）。
- 优先级色边框 `.kanban-card--pri-{priority}`、类型图标 `.kanban-card-type-icon`、Epic 名 `taskEpicName`、
  指派人头像 `getAssigneeName/Initials`、截止日期 `formatDueDate/isOverdue`、状态进度条 `taskProgressPct` 全部复用既有方法/CSS。

## 状态机
拖拽落列调用既有 `onKanbanDrop` → `api.setTaskStatus` → 本地 `tasks.update`。非法迁移（如 backlog→done）
由后端状态机拒绝并 toast 提示，与列表行内切换一致。

## 视图切换
- 工具栏 `#boardToggle` 按钮：`setBoardMode(!boardMode())`，偏好存 `localStorage.agentboard_story_view`。
- 键盘 `v`：`handleTaskKeydown` 新增 `case 'v'` 切换 `boardMode`（与命令面板提示一致）。

## 样式
`app.css` 新增看板基础布局块（`.kanban/.kanban-col/.kanban-col-header/.kanban-col-body/.kanban-card/...`），
含 `[data-theme="dark"]` 适配与列折叠态；组件级 hover/拖拽/优先级/进度样式此前已存在，直接复用。
