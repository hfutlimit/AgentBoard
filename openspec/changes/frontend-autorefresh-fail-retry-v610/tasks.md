# Tasks: 后台自动刷新失败提示与一键重试 — Epic 82 v6.10

## 实现任务
- [x] `app.ts` 新增 `retryAutoRefresh()`：复位倒计时并立即触发 `autoRefreshTick()`
- [x] `app.html` 在 `#autoRefreshBtn` 后新增失败提示条 `@if (autoRefresh() && autoRefreshFailing())`，含「自动刷新失败」文案 + `#autoRefreshRetryBtn`（点击 `retryAutoRefresh()`，`[disabled]="refreshing()"`）
- [x] `app.css` 新增 `.auto-refresh-fail*` 样式（胶囊/告警点/重试按钮，含暗色与 `prefers-reduced-motion` 降级）
- [x] `npm run build` → 部署 `agentboard/web/static/`（整目录含新 `index.html`）

## 验证任务
- [x] Playwright E2E `tests/test_epic82_v610_autorefresh_fail_retry_e2e.py` 全绿（失败提示出现 / 重试保留内容 / 成功消失 / 0 报错）
- [x] 回归 `pytest tests/test_epic30_cache.py` 8 passed
- [x] 回归 v6.8 / v6.9 刷新 E2E 全绿

## 状态流转
- Task（high）：backlog → todo → in_progress → in_review
- Story / Epic：in_review
