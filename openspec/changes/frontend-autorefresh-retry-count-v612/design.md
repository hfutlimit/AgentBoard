# Design: 后台自动刷新失败重试退避计数显示 — Epic 84 v6.12

## 现状（v6.11）
- `app.ts`：
  - `autoRefreshFailing = signal(false)`：粘性失败标记，成功同步才复位。
  - `autoRefreshCountdown = signal(30)`：每秒 `autoTick()` 递减，归零触发 `autoRefreshTick()` 并重置为 `autoRefreshSeconds`。
  - `autoRefreshTick()`：失败时 `autoRefreshFailing.set(true)`；成功时 `autoRefreshFailing.set(false)` + `lastSyncedAt` + `pulseSynced()` + 恢复 toast。
  - `retryAutoRefresh()`：立即复位倒计时并调用 `autoRefreshTick()`。
- `app.html`：`.auto-refresh-fail` 内仅显示「自动刷新失败」+ `#autoRefreshRetryBtn`「重试」。

## 改动方案（纯前端，零后端契约变更）

### 1. `app.ts`
- 新增信号：`readonly autoRefreshAttempts = signal(0);`
- `autoRefreshTick()` 失败分支（`if (this.error())` 与 `catch`）：
  - `this.autoRefreshFailing.set(true);`
  - `this.autoRefreshAttempts.update(n => n + 1);`  // 每次失败同步计一次重试
- `autoRefreshTick()` 成功分支（else）：
  - `this.autoRefreshAttempts.set(0);`  // 成功即归零
  - 其余保持（lastSyncedAt / pulseSynced / 恢复 toast）
- 不改动 `retryAutoRefresh()` 逻辑：`retryAutoRefresh()` 调 `autoRefreshTick()`，失败时会自然自增计数，保持「手动重试也算一次尝试」的语义一致性。

### 2. `app.html`
- `.auto-refresh-fail` 文案由「自动刷新失败」升级为：
  ```
  <span class="auto-refresh-fail-text">自动同步失败（第 {{ autoRefreshAttempts() }} 次）· {{ autoRefreshCountdown() }}s 后自动重试</span>
  ```
- 保留 `#autoRefreshRetryBtn`「重试」按钮与 `.auto-refresh-fail-dot`。

### 3. `app.css`
- 复用既有 `.auto-refresh-fail*` 样式；确认 `.auto-refresh-fail-text` 在暗色主题下可读（继承既有变量，无需新增大量样式，仅微调多行换行与字号以容纳更长文案）。

## 验证
- Playwright E2E：开启自动刷新 → 用 `page.route` 拦截 API 返回 500/abort 制造持续失败 → 断言 `.auto-refresh-fail` 出现且文案包含「第 N 次」「s 后自动重试」→ 等待数秒断言计数随自动重试递增 → 点击「重试」断言触发新一次同步（计数继续或恢复）→ 恢复 API 后断言失败条消失、计数归零、出现「已同步」→ 全程 0 控制台/网络错误（不计预期的 5xx 失败请求）。
