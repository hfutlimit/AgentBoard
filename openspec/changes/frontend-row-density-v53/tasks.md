# Tasks: 任务列表行密度切换（v5.3）

## 实现
- [x] `app.html` `filterbar__right` 新增 `#densityToggle` 按钮（调用 `toggleListDensity()`，文案「舒适/紧凑」+ `aria-pressed`）
- [x] `app.css` 新增 `.btn.density-toggle`（`.density-glyph` + `aria-pressed="true"` 高亮），复用既有 `.entity-list.density-compact` 紧凑规则
- [x] 前端构建 `npm run build`（managed node 22.22.2，清 `.angular/cache`，`NODE_OPTIONS=--max_old_space_size=4096`）→ cp `dist/frontend/browser/.` → `agentboard/web/static/`（新包 `main-NYXBDWD5.js` / `styles-XJWX23MR.css`，删旧 `main-62TA2BLF.js`）

## 验证
- [x] E2E `tests/test_epic66_v53_row_density_e2e.py`：默认舒适（padding 10px）→ 点击切紧凑（padding 6px）→ 再点恢复（10px）→ 刷新持久化（localStorage='compact' + 类 + 文案「紧凑」）+ 0 pageerror/console/.js+.css 404
- [x] 回归：后端 `pytest test_epic30_cache.py` 8 passed；E2E `test_epic65_v52_bulk_duplicate_e2e.py` 全绿（任务列表渲染/选择/批量无回归）

## 状态流转
- [x] Task 1121 → in_review（backlog→todo→in_progress→in_review 合法链）
- [x] Story 105 → in_review；Epic 56 → in_review
