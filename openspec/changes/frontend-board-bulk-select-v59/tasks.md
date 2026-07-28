# Tasks: 看板视图批量操作（卡片多选 + 复用批量工具栏）(Epic 72 v5.9)

## 实现任务
- [x] **T1** `app.html`：看板卡片 `<article>` 增加 `[class.selected]="selectedTasks().has(t.id)"`。
- [x] **T2** `app.html`：`.kanban-card-top` 首部新增 `.kanban-card-check` 复选框，绑定 `toggleTaskSelection(t.id)`，并 `mousedown`/`click` stopPropagation（防误开抽屉 / 防拖拽）。
- [x] **T3** `app.css`：新增 `.kanban-card-check` 与 `.kanban-card.selected`（含暗色），复用品牌色变量。
- [x] **T4** 复用：勾选后既有共享 `.bulk-action-bar` 在看板视图自动出现，全部批量操作（状态机感知状态/优先级/类型/指派/截止/复制/删除）立即可用，无需新增逻辑。
- [x] **T5** 构建：`npm run build`（managed node 22.22.2，清 `.angular/cache`，`NODE_OPTIONS=--max_old_space_size=4096`）→ cp 至 `agentboard/web/static/`，删除旧 `main-*.js`（新 `main-GS2YFXXM.js`）。
- [x] **T6** 验证：Playwright E2E `tests/test_epic72_v59_board_bulk_select_e2e.py` 全绿（勾选→选中态+不触发抽屉+批量工具栏「2 项已选」→状态机感知批量改状态 API 复核→Esc 清除→0 错误）。
- [x] **T7** 回归：`pytest test_epic30_cache.py` 8 passed + v5.8 看板视图 E2E 全绿；v4.2 快速查看 E2E 因硬编码 28080（已宕机）失败为预先存在端口漂移，非本次回归。

## 验收结论
- 看板视图每张卡片含选择框；勾选后卡片进入选中态且不复用快速查看；共享批量工具栏出现并支持状态机感知的批量状态变更（API 复核两张任务均变为 done）。
- 0 pageerror / console error / .js+.css 404。
- 追踪：task 1127 / story 111 / epic 62 均置 **in_review**。
