# 任务清单：任务列表筛选预设（Epic 43 v3.1）

## Task 1106 — 筛选预设 UI 与逻辑（priority: high）
- [x] `app.ts`：新增 `FilterPreset` 接口 + `filterPresets / presetName / presetOpen` 信号 + `loadFilterPresets/persistFilterPresets` 辅助
- [x] `app.ts`：新增 `saveFilterPreset()` / `applyFilterPreset(idx)` / `deleteFilterPreset(idx)` / `togglePresetOpen()`
- [x] `app.html`：工具条新增「📑 预设」按钮 + 浮层（保存输入 / 预设列表 / 应用 / 删除）
- [x] `app.css`：`.preset-wrap / .preset-panel / .preset-item / .preset-apply / .preset-del` 等样式
- [x] `npm run build` 通过（无 TS 错误），产物 cp 至 `agentboard/web/static/`
- [x] Playwright E2E 验证：保存→应用→删除 全链路 + 持久化 + 0 控制台/页面/404 错误
- [x] 后端回归：`pytest test_epic30_cache.py` 8 passed（无后端改动，零回归）
- [x] 提交 + `git push origin main`

## 验收标准
- 保存当前筛选组合后，面板出现该预设且数量徽标 +1；
- 点击预设名称即还原对应筛选（含搜索 / 只看我）；
- 刷新页面后预设仍在（localStorage 持久化）；
- 删除预设后从列表消失；
- 全程无 JS 报错 / 控制台错误 / .js+.css 404。
