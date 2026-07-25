# Change: 快速查看抽屉内联编辑标题与描述（v4.3）

## Why
任务列表「行内快速查看抽屉（Quick View Drawer, v4.2）」已支持查看面包屑/四字段/子任务进度/描述，并可在抽屉内快速改状态/优先级/指派/截止。但标题与描述仍只能进详情页才能改，打断查看流。需要在抽屉内直接行内编辑标题与描述，保存经 `updateTask` 即时刷新列表，与 Jira 式体验对齐。

## What Changes
- `frontend/src/app/app.ts`：复用既有 `qvEditingTitle/qvEditTitle/qvEditingDesc/qvEditDesc` 信号与 `startQvEditTitle/saveQvTitle/cancelQvEditTitle/startQvEditDesc/saveQvDesc/cancelQvEditDesc` 方法（v4.2 已落地 TS 逻辑）。
- `frontend/src/app/app.html`：描述区新增编辑按钮 + textarea 编辑态（标题编辑态已在 v4.2 落地）；采用 `@if/@else if/@else` 在「展示 / 编辑 / 空态」间切换。
- `frontend/src/app/app.css`：补齐 `.qv-edit-btn`、`.qv-title-input`、`.qv-title-edit`、`.qv-edit-actions`、`.qv-desc-head`、`.qv-desc-edit`、`.qv-desc-input`（含 dark 主题），使行内编辑 UI 与整体视觉一致。
- 纯前端，零后端契约变更（`updateTask(id,{title|description})` 沿用既有 PATCH 端点）。

## Impact
- 仅前端组件样式/模板变更，不影响任何 API 契约或后端逻辑。
- 复用既有的 `api.updateTask` 与 `tasks.update` 局部刷新，列表与抽屉同步即时生效。
- 新增 E2E `tests/test_epic56_v43_inline_edit_title_desc_e2e.py` 覆盖标题/描述编辑 + 取消无副作用。

## Status
Implemented（in_review）
