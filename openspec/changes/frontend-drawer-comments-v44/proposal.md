# Change: 快速查看抽屉评论区（v4.4）

## Why
任务列表「行内快速查看抽屉（Quick View Drawer, v4.2/v4.3）」已支持查看面包屑/标题/四字段/子任务进度/描述，并在抽屉内行内编辑标题与描述、快速改状态/优先级/指派/截止。但评论仍需进详情页才能查看与回复，打断查看流。需要在抽屉内直接展示任务评论列表并支持行内快速添加/删除评论，与 Jira 式体验对齐，让协作上下文不打断。

## What Changes
- `frontend/src/app/app.ts`：新增 `qvComments`/`qvCommentDraft`/`qvLoadingComments` 信号，以及 `qvLoadComments()`（打开抽屉时加载 `GET /api/tasks/{id}/comments`）、`qvAddComment()`（行内添加 `POST /api/tasks/{id}/comments`）、`qvDeleteComment(id)`（删除 `DELETE /api/comments/{id}`）；`openQuickView` 打开时触发评论加载，`closeQuickView` 清空评论状态。复用既有 `commentAuthor()`、`renderMarkdown()`、`notify()`。
- `frontend/src/app/app.html`：抽屉 `.qv-body` 末尾新增 `.qv-comments` 评论区——评论计数徽标、加载中/空态、评论列表（作者 + `timeAgo` + Markdown 渲染正文 + 删除按钮）、底部 `textarea` 行内添加框（⌘/Ctrl+Enter 发送）。
- `frontend/src/app/app.css`：补齐 `.qv-comments`、`.qv-comment`、`.qv-comment-head/author/time/del`、`.qv-comment-body`（Markdown 子元素样式）、`.qv-comment-compose`、`.qv-comment-input`、`.qv-comment-actions`（含 dark 主题）。
- 纯前端，零后端契约变更（评论 API 早已存在：`listComments`/`addComment`/`deleteComment`）。

## Impact
- 仅前端组件样式/模板/逻辑变更，不影响任何 API 契约或后端逻辑。
- 复用既有的 `api.listComments`/`api.addComment`/`api.deleteComment`（经 `firstValueFrom` 调用），添加后即时刷新列表。
- 新增 E2E `tests/test_epic57_v44_drawer_comments_e2e.py` 覆盖查看渲染（Markdown）、行内添加、行内删除 + API 复核。

## Status
Implemented（in_review）
