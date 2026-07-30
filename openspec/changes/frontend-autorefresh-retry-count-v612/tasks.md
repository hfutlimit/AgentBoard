# Tasks: 后台自动刷新失败重试退避计数显示 — Epic 84 v6.12

## 实现任务
- [x] `app.ts` 新增 `autoRefreshAttempts` 信号（默认 0）
- [x] `app.ts` `autoRefreshTick()` 失败分支（error / catch）自增 `autoRefreshAttempts`，成功分支归零
- [x] `app.html` `.auto-refresh-fail` 文案升级为「自动同步失败（第 N 次）· M 秒后自动重试」+ 保留「重试」按钮
- [x] `app.css` 微调失败条文案样式（含暗色，复用既有变量）
- [x] `npm run build` → 部署 `agentboard/web/static/`（整目录含新 `index.html`）

## 验证任务
- [x] Playwright E2E `tests/test_epic84_v612_autorefresh_retry_count_e2e.py` 全绿（失败条计数+倒计时渲染 / 计数随重试递增 / 点击重试触发 / 恢复后归零+已同步 / 0 报错）
- [x] 回归 `pytest tests/test_epic30_cache.py` 8 passed
- [x] 回归 v6.6~v6.11 刷新 E2E 全绿

## 状态流转
- Task 905（high）：backlog → todo → in_progress → in_review
- Story 153 / Epic 95：in_review
