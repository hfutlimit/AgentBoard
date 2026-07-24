# Tasks: 任务列表行内快速指派 (v3.8 / Epic 51)

## Task 991 — Epic 51 v3.8: 任务列表行内快速指派（前端，high）
- [x] `app.ts` 新增 `assignMenuTaskId` / `assignMenuPos` 信号 + `assignMenuTask()` computed
- [x] 新增 `openAssignMenu`（点击防跳转；`members()` 为空时按 `task.project_id` 懒加载 `loadMembers`）+ `closeAssignMenu` + `quickAssign`（调 `api.updateTask(id,{assignee_id})` 后 `tasks.update` 局部刷新 + `notify`）
- [x] `app.html` 指派人头像外层包可点击 `.assignee-pill`（stopPropagation/preventDefault 防跳转，键盘可达，镜像 v3.4 状态徽章）
- [x] 列表视图内新增固定浮层 `.assign-menu`（遍历 `members()` + 「未指派」项，当前指派 `active` 高亮）+ `.status-menu-backdrop` 遮罩关闭（复用 v3.4 样式）
- [x] `app.css` 新增 `.assignee-pill` / `.assign-menu-item.active` 样式（含 dark 主题）
- [x] `npm run build` 通过；产物 `main-DXSJYRMB.js` cp 至 `agentboard/web/static/`，旧 `main-WXZPDYFU.js` 清理
- [x] Playwright E2E `test_epic51_v38_inline_assign_e2e.py`：登录 admin → /story/25 → 点击指派人头像 → 浮层列出成员 → 点成员指派（API 复核 assignee_id 变更）→ 点「未指派」取消（API 复核 null）→ 遮罩关闭 / 即时更新 / 0 console·page·js-css 报错 → PASS
- [x] 回归：`pytest test_epic30_cache.py` 7 passed/1 skipped；E2E v3.4 状态切换 / v3.7 截止日期分组 全绿（无回归）
- [x] 经 REST 创建 Epic 51 / Story 189 / Task 991，状态合法迁移置 `in_review`（project ADV38）
