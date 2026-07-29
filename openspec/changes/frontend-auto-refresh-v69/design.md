# Design: 任务视图后台自动轮询刷新 (v6.9)

## 状态与计时
- `autoRefresh = signal(isAutoRefreshEnabled())`：开关，持久化键 `agentboard_auto_refresh`（`'on'` 表示开启）。
- `autoRefreshSeconds = 30`：轮询间隔（秒），类字段，模板可直接引用。
- `autoRefreshCountdown = signal(autoRefreshSeconds)`：倒计时（秒），每秒减 1，归零触发一次同步并复位。
- `lastSyncedAt = signal<number|null>(null)`：上次成功同步时间戳。
- `autoRefreshFailing = signal(false)`：连续失败时置位（低调告警）。
- `autoTimer`：1s 心跳 `setInterval` 句柄，登出/关闭时清理。

## 心跳与同步
- `autoTick()`：`if (!autoRefresh() || document.hidden) return;` 否则倒计时递减；归零时复位并调用 `autoRefreshTick()`。
- `autoRefreshTick()`：`if (refreshing()) return;` 置 `refreshing=true` → `await loadRoute(false)` →
  若 `error()` 置 `autoRefreshFailing=true`，否则写 `lastSyncedAt` 并清 `failing`；`finally` 复位 `refreshing`。
- `toggleAutoRefresh()`：翻转开关 + 持久化 + `startAutoTimer`/`stopAutoTimer`。
- 与手动刷新互斥：`refreshing` 信号由两者共享，任一方进行中另一方均被 `refreshing()` 守卫跳过。

## UI（工具栏，列表/看板共用）
- `#autoRefreshBtn`：图标 + 「自动」文案；`[class.active]=autoRefresh()`；开启态绿色描边/底色。
- `.auto-refresh-dot`：状态点，开启呼吸脉冲，失败时琥珀；`@if(autoRefresh() && !refreshing())` 显示 `.auto-refresh-count` 倒计时。
- title 含 `lastSyncedLabel()` 相对时间。

## 生命周期
- 初始化：若 `autoRefresh()` 已开启则 `startAutoTimer()`（在健康检查轮询之后）。
- `logout()`：`stopAutoTimer()` 防泄漏。

## 暗色与降级
- `[data-theme="dark"]` 适配激活底色；`prefers-reduced-motion` 关闭脉冲/旋转动画。
