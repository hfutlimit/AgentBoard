# Proposal: 任务列表看板视图渲染 (Epic 71 v5.8)

## 问题
任务列表仅有「列表视图」。`boardMode` 信号、`setBoardMode()`、看板 CSS、拖拽改状态处理器
（`onKanbanDragStart/Over/Leave/Drop/End`）、列折叠、卡片优先级色边框/进度等基础设施早已落地，
但 `app.html` 仅有 `@if (!boardMode())` 列表分支，**缺少 `@else` 看板渲染分支**，
且工具栏无视图切换入口、键盘 `v` 提示未接线。看板功能实际从未可用。

## 目标
补齐看板视图的「最后一公里」：
1. 工具栏新增「列表 / 看板」切换按钮（持久化偏好）。
2. `app.html` 新增 `@else` 看板分支：按 `statuses` 分桶渲染列，卡片可拖拽改状态、点击打开快速查看。
3. `handleTaskKeydown` 接线 `v` 键切换视图。
4. `app.css` 补看板基础布局样式（含暗色主题、列折叠态、拖拽反馈）。

## 非目标
- 不改动后端契约（`statuses`/`tasksForStatus`/`onKanbanDrop` 等全部复用既有）。
- 不引入分组/筛选在看板下的新交互（看板本身按状态分桶）。

## 风险
低。纯前端、零契约变更；列表视图模板字节级未变，仅 `@if` 收尾改为 `@if/@else`。
