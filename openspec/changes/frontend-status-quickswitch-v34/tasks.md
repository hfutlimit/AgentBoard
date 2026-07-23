# Tasks: 任务列表行内快速状态切换 (v3.4 / Epic 47)

## Task 945 — Epic 47 v3.4: 任务列表行内快速状态切换（前端，high）
- [x] `app.ts` 新增 `statusTransitions` 状态机镜像 + `validNextStatuses(task)` + `statusMenuTaskId` / `statusMenuPos` 信号
- [x] 新增 `openStatusMenu` / `closeStatusMenu` / `quickSetStatus`（调用 `api.setTaskStatus` 后 `this.tasks.update` 局部刷新 + `notify`）
- [x] 任务行状态徽章升级为可点击 `.status-pill`（stopPropagation/preventDefault 防跳转，键盘可达）
- [x] 列表视图内新增固定浮层 `.status-menu`（合法目标 + 色点）+ `.status-menu-backdrop`（点击关闭）
- [x] `app.css` 新增 `.status-pill` / `.status-menu--fixed` / `.status-menu-item` / `.status-dot` / `.status-menu-backdrop`（含 dark 主题）
- [x] `npm run build` 通过；产物 `main-EXWGHMZD.js` cp 至 `agentboard/web/static/`，旧 main 清理
- [x] Playwright E2E `test_epic47_v34_status_quick_switch_e2e.py`：backlog→1 项(待办) / todo→3 项 / 遮罩关闭 / 即时更新 / 0 console·page·js-css 报错 → PASS
- [x] 回归：`pytest test_epic30_cache.py` 7 passed/1 skipped；E2E v3.3 排序 / v2.7 指派人 全绿（无回归）
- [x] 经 REST 创建 Epic 47 / Story 185 / Task 945，状态合法迁移置 `in_review`（project 110 / epic 118）
