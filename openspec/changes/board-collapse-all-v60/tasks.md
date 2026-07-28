# Tasks: 看板视图列全折叠/全展开 (v6.0)

- [x] 在 `app.ts` 新增 `allColumnsCollapsed` computed + `collapseAllColumns` / `expandAllColumns` 方法
- [x] 在 `app.html` 工具栏（仅看板模式）新增 `#boardColsToggle` 切换按钮
- [x] 在 `app.css` 新增 `.btn.board-cols-toggle` 样式（镜像 density-toggle）
- [x] `npm run build` 并 cp 至 `agentboard/web/static/`（产物 `main-A6KMV6OY.js`）
- [x] Playwright E2E `tests/test_epic73_v60_board_collapse_e2e.py` 全绿（折叠/展开/持久化/0 错误）
- [x] 回归：pytest test_epic30_cache.py 8 passed + v5.8/v5.9 看板 E2E 全绿
- [x] 追踪实体经 REST 置 in_review：Task 1128 / Story 112 / Epic 63

## 验证结论
- 看板列一键全折叠（7 列全部 `.collapsed`）、按钮文案切换、刷新持久化均通过。
- 全程 0 pageerror / console error / .js+.css 404。
