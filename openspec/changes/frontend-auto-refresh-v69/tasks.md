# Tasks: 任务视图后台自动轮询刷新 (v6.9)

## 实现任务
- [x] app.ts：新增 `autoRefresh`/`autoRefreshSeconds`/`autoRefreshCountdown`/`lastSyncedAt`/`autoRefreshFailing` 信号与 `autoTimer` 字段
- [x] app.ts：新增 `isAutoRefreshEnabled`/`toggleAutoRefresh`/`startAutoTimer`/`stopAutoTimer`/`autoTick`/`autoRefreshTick`/`lastSyncedLabel`
- [x] app.ts：初始化中按偏好启动轮询；`logout()` 清理定时器
- [x] app.html：工具栏新增 `#autoRefreshBtn` 开关 + 倒计时徽标 + 同步状态点
- [x] app.css：`.auto-refresh-btn`/`.auto-refresh-dot`/`.auto-refresh-count` 样式（含暗色与 reduced-motion 降级）
- [x] 构建并部署静态产物至 `agentboard/web/static/`

## 验证任务
- [x] Playwright e2e：开启→倒计时递减→30s 触发静默同步（视图不闪/内容保持）→状态点绿色脉冲
- [x] Playwright e2e：关闭→停表；刷新持久化；tab 隐藏冻结倒计时
- [x] 回归：cache 单测 + 既有看板/chips/perf/preset/refresh E2E 无回归
