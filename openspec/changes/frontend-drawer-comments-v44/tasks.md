# Tasks: 快速查看抽屉评论区（v4.4）

## 实现任务清单

- [x] T1 前端状态与逻辑：`app.ts` 新增 `qvComments`/`qvCommentDraft`/`qvLoadingComments` 信号与 `qvLoadComments`/`qvAddComment`/`qvDeleteComment` 方法；`openQuickView` 触发加载、`closeQuickView` 清空（复用 `api.listComments`/`addComment`/`deleteComment`、`commentAuthor`、`renderMarkdown`、`notify`）。
- [x] T2 模板：在 `app.html` 抽屉 `.qv-body` 末尾新增 `.qv-comments` 区块（计数徽标、加载中/空态、`@for` 评论列表含作者/`timeAgo`/Markdown 正文/删除按钮、底部 `textarea` 行内添加框 + 发送按钮，⌘/Ctrl+Enter 发送）。
- [x] T3 样式：`app.css` 补齐 `.qv-comments` 系列样式（含 dark 主题）与 Markdown 子元素渲染样式。
- [x] T4 构建与部署：`npm run build`（managed node 22.22.2，清 `.angular/cache`）→ cp `dist/frontend/browser/.` → `agentboard/web/static/`，删除旧 `main-*.js`。
- [x] T5 验证：Playwright `tests/test_epic57_v44_drawer_comments_e2e.py` 全绿（0 pageerror/console/.js+.css 404）；回归 `pytest test_epic30_cache.py`（8 passed）+ E2E v4.2/v4.3（全绿）。
- [x] T6 状态流转：Task 1059 / Story 203 / Epic 130 经 `backlog→todo→in_progress→in_review` 合法链置 **in_review**。

## 追踪实体（REST 兜底，MCP 连接器断开）
- project 124 (AUTODEV57) → epic 130 (Epic 57 v4.4) → story 203 → task 1059 (high)
