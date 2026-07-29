# Design: 后台自动刷新成功轻提示 — Epic 83 v6.11

## 状态与数据流
- 复用既有信号：`autoRefresh`（开关）、`autoRefreshFailing`（连续同步失败置位，粘性）、`refreshing`（手动/自动刷新互斥锁）、`lastSyncedAt`（上次成功时间戳，驱动 `lastSyncedLabel()`）。
- 新增 `autoSynced` 瞬时信号：同步成功时置 `true`，1.5s 后由 `pulseSynced()` 内 `setTimeout` 复位为 `false`，用于点亮绿点与「已同步」胶囊的轻提示，不持久、不每周期打扰。
- 在 `autoRefreshTick()` 进入本拍前捕获 `wasFailing = this.autoRefreshFailing()`：
  - 同步成功且 `wasFailing` 为真 → 弹「后台已恢复同步」成功 toast（与 v6.10 失败提示联动）。
  - 同步成功 → `pulseSynced()` 点亮绿点 + 短暂「已同步」胶囊。

## 交互流程
1. `autoRefreshTick` 调 `loadRoute(false)`；`this.error()`/抛异常 → `autoRefreshFailing.set(true)`（保持失败提示条）。
2. 成功分支：
   - `lastSyncedAt.set(Date.now())`；`autoRefreshFailing.set(false)`（失败提示条随状态消失）。
   - `pulseSynced()` → `autoSynced.set(true)`（绿点 `.synced` 闪烁 + `.auto-refresh-ok`「已同步」胶囊出现，1.5s 后熄灭）。
   - `if (wasFailing) this.notify('后台已恢复同步', 'success')` → 仅恢复瞬间弹一次成功 toast。
3. 正常周期（无前置失败）成功：仅点亮绿点 + 短暂「已同步」，不弹 toast。

## 模板与样式
- `app.html`：
  - `#autoRefreshBtn` 内 `.auto-refresh-dot` 增加 `[class.synced]="autoSynced() && autoRefresh()"`。
  - v6.10 失败提示条之后新增 `@if (autoRefresh() && autoSynced()) { <span class="auto-refresh-ok">已同步</span> }`。
- `app.css`：
  - `.btn.auto-refresh-btn.active .auto-refresh-dot.synced` 用 `auto-synced-flash` 动画（优先级高于常驻 `auto-pulse`，特异性更高）。
  - `.auto-refresh-ok` 胶囊样式（成功绿底/边，淡入），含 `[data-theme="dark"]` 适配与 `prefers-reduced-motion` 降级。

## 验证
- Playwright E2E `tests/test_epic83_v611_autorefresh_success_hint_e2e.py`：
  - 开启自动刷新 → 拦截 `/api/projects` 返回 500 触发失败提示条（v6.10 既有）。
  - 解除拦截并点击 `#autoRefreshRetryBtn` → 同步成功 → 失败提示条消失，且出现「后台已恢复同步」成功 toast（`.toast.success` 含该文案）。
  - 同步成功瞬间 `.auto-refresh-dot.synced` 与 `.auto-refresh-ok`「已同步」出现（轻提示）。
  - 全程 0 pageerror / console error / .js·.css 404（刻意 500 的网络报错属预期副作用，已在监听中排除）。
- 回归：`pytest tests/test_epic30_cache.py` 8 passed + v6.6~v6.10 刷新 E2E 全绿。
