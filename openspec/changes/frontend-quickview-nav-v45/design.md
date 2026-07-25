# Design: 快速查看抽屉任务前后导航（v4.5）

## 交互模型
- 抽屉打开后，头部右侧出现 `‹` / `›` 两个导航按钮，位于关闭按钮左侧。
- 上一项 = `visibleTasks()` 中当前任务的上一索引；下一项 = 下一索引。
- 到首行时「上一项」禁用，到末行时「下一项」禁用（与列表顺序一致，受筛选/分组/排序影响）。
- 键盘：`[` 上一项、`]` 下一项，仅在抽屉打开且焦点不在输入框/文本域/下拉时生效（与既有 `handleTaskKeydown` 的输入框守卫一致）。
- 切换任务复用 `openQuickView(nextTask)`，自然触发评论重新加载与面包屑更新。

## 状态边界处理
- `qvHasPrev()`：`idx > 0` 才有上一项。
- `qvHasNext()`：`idx >= 0 && idx < list.length - 1` 才有下一项。
- `qvNav(delta)`：越界早退，无副作用。

## 编辑态清理
- `openQuickView` 在设置 `qvTaskId` 后立即把 `qvEditingTitle` / `qvEditingDesc` 置 `false`，确保从 A 切到 B 时 B 不会残留 A 的标题/描述编辑态（修复 v4.3 编辑态串台隐患）。

## 视觉
- `.qv-nav` 复用 `.qv-close` 的尺寸/圆角/配色，disabled 态降透明度并禁用指针；dark 主题下调整边框/文字色。

## 复用与零契约变更
- 不新增任何 API；全部基于既有 `visibleTasks()` / `qvTask()` / `openQuickView()`。
- 与 v3.2 列表快捷键、`handleTaskKeydown` 的输入框守卫风格统一。
