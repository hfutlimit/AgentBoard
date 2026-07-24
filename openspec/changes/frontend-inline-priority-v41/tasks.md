# Tasks: 任务列表行内快速修改优先级 (v4.1 / Epic 54)

## Task 1017 — Epic 54 v4.1: 任务列表行内快速修改优先级（前端，high）
- [x] `app.ts` 新增 `priorityMenuTaskId` / `priorityMenuPos` 信号 + `priorityMenuTask()` computed（镜像 v3.4）
- [x] 新增 `openPriorityMenu`（点击防跳转，获取徽章 rect 定位浮层）+ `closePriorityMenu` + `quickSetPriority`（调 `api.updateTask(id,{priority})` 后 `tasks.update` 局部刷新 + `notify`）
- [x] `app.html` 优先级徽章包裹为可点击 `.priority-pill`（`preventDefault` 防跳转，键盘可达，镜像 v3.4 状态徽章，加 `▾` caret）
- [x] 列表视图内新增固定浮层 `.priority-menu`（遍历 `priorities` 渲染 5 档 + 当前 `active` 高亮）+ `.status-menu-backdrop` 遮罩关闭（复用 v3.4 样式）
- [x] `app.css` 新增 `.priority-pill` / `.priority-dot` / `.priority-menu-item.active` 样式（含 dark 主题）
- [x] `npm run build` 通过；产物 `main-4DFFXVGN.js` cp 至 `agentboard/web/static/`，旧 `main-D3SGJYRX.js` 清理
- [x] Playwright E2E `test_epic54_v41_inline_priority_e2e.py`：登录 admin → /story/25 → 点击优先级徽章 → 浮层列出 5 档 → 点档位（API 复核 priority 变更）→ 遮罩关闭 / 即时更新 / 0 console·page·js-css 报错 → PASS
- [x] 回归：`pytest test_epic30_cache.py` 7 passed/1 skipped；E2E v3.4 / v3.8 / v3.9 全绿（无回归）
- [x] 经 REST 创建 Epic 54 / Story 200 / Task 1017，状态合法迁移置 `in_review`（project ADV41）
