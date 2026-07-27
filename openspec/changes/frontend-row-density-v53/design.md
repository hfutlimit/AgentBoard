# Design: 任务列表行密度切换（v5.3）

## 现状分析
- 能力骨架已存在：`listDensity` 信号（app.ts:90）、`toggleListDensity()`（app.ts:2884）、`.entity-list.density-compact` 样式（app.css:973）。
- 缺口：`.entity-list` 已通过 `[class.density-compact]="listDensity() === 'compact'"` 绑定（app.html:1387），但工具条无按钮调用 `toggleListDensity()` → 用户无法触发。

## 设计决策
1. **复用既有信号/方法，仅补 UI**：不新增状态或持久化逻辑，避免契约漂移；按钮直接绑定 `toggleListDensity()`。
2. **按钮位置**：置于 `filterbar__right` 的「折叠」开关之后，与排序/分组/预设等视图控件同位，符合用户心智。
3. **可访问性**：`id="densityToggle"` 便于 E2E 定位；`aria-pressed` 反映当前是否紧凑；文案「舒适/紧凑」提供即时状态反馈。
4. **样式**：紧凑态由既有 `.entity-list.density-compact .entity-item--rich { padding: 6px 14px }` 主导（默认 `10px 14px`），仅补按钮自身样式，无布局破坏。

## 验证策略
- Playwright 断言**计算样式 padding**（`getComputedStyle`）而非像素高度，规避字体加载/过渡导致的亚像素抖动；点击后等待 `transition: all .2s` 完成（350ms）再测量。
- 覆盖：默认舒适、切紧凑、恢复舒适、刷新持久化（localStorage + 类 + 按钮文案）四类不变量。
