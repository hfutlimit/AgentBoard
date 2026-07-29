# Tasks: 后台自动刷新成功轻提示 — Epic 83 v6.11

## 实现任务
- [x] `app.ts` 新增 `autoSynced` 信号与 `pulseSynced()`（1.5s 自动熄灭）
- [x] `app.ts` `autoRefreshTick()` 进入本拍前捕获 `wasFailing`，成功分支调用 `pulseSynced()`，恢复时 `notify('后台已恢复同步','success')`
- [x] `app.html` `.auto-refresh-dot` 增加 `[class.synced]`；v6.10 失败条后新增 `.auto-refresh-ok`「已同步」轻提示
- [x] `app.css` 新增 `.synced` 绿点闪烁动画与 `.auto-refresh-ok` 胶囊样式（含暗色与 `prefers-reduced-motion` 降级）
- [x] `npm run build` → 部署 `agentboard/web/static/`（整目录含新 `index.html`）

## 验证任务
- [x] Playwright E2E `tests/test_epic83_v611_autorefresh_success_hint_e2e.py` 全绿（恢复成功 toast / 轻提示 / 0 报错）
- [x] 回归 `pytest tests/test_epic30_cache.py` 8 passed
- [x] 回归 v6.6~v6.10 刷新 E2E 全绿

## 状态流转
- Task（high）：backlog → todo → in_progress → in_review
- Story / Epic：in_review
