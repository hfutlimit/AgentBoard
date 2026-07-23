# Tasks: 批量状态变更状态机感知 (v3.5 / Epic 48)

## Task 946 — Epic 48 v3.5: 批量状态变更状态机感知（前端，high）
- [x] `app.ts` 新增 `readonly bulkLegalStatuses = computed<string[]>`：对 `selectedTasks` 取各自 `statusTransitions` 做逐任务交集，空选/无共同项返回 `[]`
- [x] `app.html` 批量状态面板改为 `@if (bulkLegalStatuses().length>0)` 遍历交集渲染按钮；`@else` 渲染 `.muted` 空态提示「所选任务状态无共同可流转目标（受状态机限制）」；取消按钮保留
- [x] 复用既有 `statusTransitions` / `bulkUpdateStatus` / `statusLabel`，零后端契约变更
- [x] `npm run build` 通过；产物 `main-JABFCBHD.js` cp 至 `agentboard/web/static/`，旧 main 清理
- [x] Playwright E2E `test_epic48_v35_bulk_status_fsm_e2e.py`：选 `todo+todo+in_progress`→仅「完成」；选 `backlog+todo`→0 按钮+空态提示；0 console·page·js-css 报错 → PASS
- [x] 回归：`pytest test_epic30_cache.py` 7 passed/1 skipped；E2E v3.4 行内状态切换全绿（无回归）
- [x] 经 REST 创建 Epic 48 / Story 186 / Task 946，状态合法迁移置 `in_review`（project 111 / epic 119）
