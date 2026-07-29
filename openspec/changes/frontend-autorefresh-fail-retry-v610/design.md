# Design: 后台自动刷新失败提示与一键重试 — Epic 82 v6.10

## 状态与数据流
- 复用既有信号：`autoRefresh`（开关）、`autoRefreshFailing`（连续同步失败置位）、`refreshing`（手动/自动刷新进行中互斥锁）、`autoRefreshCountdown`。
- 新增方法 `retryAutoRefresh()`：当 `autoRefresh() && !refreshing()` 时，将 `autoRefreshCountdown` 复位为 `autoRefreshSeconds`，并 `void this.autoRefreshTick()` 立即触发一次静默同步；`autoRefreshTick` 内部已在成功时清除 `autoRefreshFailing`、失败时保持置位，故提示条随状态自动显隐，无需额外状态。

## 交互流程
1. 自动轮询在 `autoRefreshTick` 中调用 `loadRoute(false)`；若 `this.error()` 或抛异常 → `autoRefreshFailing.set(true)`。
2. 模板 `@if (autoRefresh() && autoRefreshFailing())` 渲染失败提示条：
   - `.auto-refresh-fail`（胶囊，琥珀底/边，淡入动画）
   - `.auto-refresh-fail-dot`（闪烁告警点）
   - `.auto-refresh-fail-text` = 「自动刷新失败」
   - `#autoRefreshRetryBtn` `.auto-refresh-retry`（点击 `retryAutoRefresh()`，`[disabled]="refreshing()"`）
3. 点击重试 → 立即静默同步；成功后 `autoRefreshFailing` 清零 → 提示条消失；失败时保持 → 按钮保持可重试。

## 模板与样式
- 插入位置：`app.html` `#autoRefreshBtn` 之后、`preset-wrap` 之前（列表/看板共用 filter 区）。
- 样式写入 `frontend/src/app/app.css`：`.auto-refresh-fail*` 含暗色 `[data-theme="dark"]` 适配、`prefers-reduced-motion` 降级（关闭淡入/闪烁/按钮过渡）。

## 验证
- Playwright E2E `tests/test_epic82_v610_autorefresh_fail_retry_e2e.py`：
  - 开启自动刷新 → 拦截 `/api/projects` 返回 500 触发失败 → 出现 `.auto-refresh-fail` 提示条与 `#autoRefreshRetryBtn`；
  - 点击重试 → 仍失败时提示条保留且任务内容不丢失；解除拦截后由轮询或重试成功 → 提示条消失；
  - 全程 0 pageerror / console error / .js·.css 404（刻意 500 的网络报错属预期副作用，已在监听中排除）。
- 回归：`pytest tests/test_epic30_cache.py` 8 passed + v6.8/v6.9 刷新 E2E 全绿。
