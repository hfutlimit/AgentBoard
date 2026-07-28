# Tasks: 看板视图列内按维度子分组 (Epic 74 v6.1)

## Task 1136: 看板视图按维度子分组（high）
- [x] app.ts 新增 `boardSubGroups(status)`，复用 `taskGroupBy`/`groupLabel`/`dueBucket`/`priorities` 分桶逻辑，与列表 `groupedTasks` 行为一致；空列返回 `[]`。
- [x] app.html 看板 `.kanban-col-body` 由平铺 `@for` 改为 `@for (grp of boardSubGroups(s))`：有 `grp.key` 时渲染子分组头（标签+计数），卡片移入 `.kanban-subgroup-body`；`@empty` 保留「暂无任务」。
- [x] styles.css 补 `.kanban-subgroup*` 样式（含暗色、`--surface-2/--border/--brand-*` 主题变量）。
- [x] `npm run build` → `main-FOZ5QS6F.js` / `styles-W5HB4YXQ.css` 部署至 `agentboard/web/static/`，删除旧 `main-A6KMV6OY.js`。
- [x] Playwright E2E `tests/test_epic74_v61_board_subgroup_e2e.py` 全绿（任务→in_review 验收）。
- [x] 回归：`pytest test_epic30_cache.py` 8 passed；E2E v5.8/v5.9/v6.0 看板套件全绿（拖拽/批量/折叠无回归）。
