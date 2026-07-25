# Change: 快速查看抽屉任务前后导航（v4.5）

## Why
任务列表「行内快速查看抽屉（Quick View Drawer, v4.2~v4.4）」已支持查看面包屑/标题/四字段/子任务进度/描述，并在抽屉内行内编辑标题与描述、快速改状态/优先级/指派/截止、查看并添加/删除评论。但在抽屉内逐条 triage 时，每次都要关闭抽屉再在列表中找下一条，打断查看流。需要像 Jira / Linear 一样，在抽屉内直接「上一项 / 下一项」切换任务，让审核与批注连续进行。

## What Changes
- `frontend/src/app/app.ts`：新增 `qvHasPrev()` / `qvHasNext()`（基于 `visibleTasks()` 当前索引判断边界）与 `qvNav(delta)`（调用既有 `openQuickView` 在可见任务间切换）；新增 `onDrawerKeydown(event)` 处理抽屉内键盘导航（`[` 上一项 / `]` 下一项，输入框聚焦时不触发）；`openQuickView` 切换任务时清空残留的行内编辑态（`qvEditingTitle` / `qvEditingDesc`），避免编辑态串到下一任务。
- `frontend/src/app/app.html`：抽屉头部新增 `.qv-nav-group`（上一项 `‹` / 下一项 `›` 两个按钮，到边界时 `disabled`）；`<aside>` 绑定 `(document:keydown)="onDrawerKeydown($event)"`。
- `frontend/src/app/app.css`：补齐 `.qv-nav-group` / `.qv-nav` 样式（含 dark 主题），复用 `.qv-close` 视觉语言。
- 纯前端，零后端契约变更（复用既有 `visibleTasks` / `openQuickView` / `qvTask`）。

## Impact
- 仅前端组件样式/模板/逻辑变更，不影响任何 API 契约或后端逻辑。
- 复用既有的 `visibleTasks()` 计算属性，导航顺序与当前筛选/分组/排序完全一致。
- 新增 E2E `tests/test_epic58_v45_drawer_nav_e2e.py` 覆盖按钮与键盘两种导航、边界禁用、Esc 关闭。

## Status
Implemented（in_review）
