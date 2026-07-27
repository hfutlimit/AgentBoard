# Change: 任务列表行密度切换（紧凑/舒适）（v5.3）

## Why
任务列表密度切换能力此前已部分落地：`listDensity` 信号（读取 `localStorage['agentboard_list_density']`，默认 `comfortable`）、`toggleListDensity()` 方法、以及 `.entity-list.density-compact` 的紧凑样式均已存在，但任务视图工具条**缺少触发该能力的切换按钮**——用户无法实际操作密度切换，功能处于「半成品」状态。补齐工具条切换按钮，使该能力真正可用、可持久化。

## What Changes
- `frontend/src/app/app.html`：在任务视图 `filterbar__right`（「折叠」开关之后）新增 `#densityToggle` 按钮，调用既有 `toggleListDensity()`；按钮文案随状态在「舒适 / 紧凑」间切换，并带 `aria-pressed` 可访问性属性。
- `frontend/src/app/app.css`：新增 `.btn.density-toggle`（含 `.density-glyph` 与 `aria-pressed="true"` 高亮态）样式，复用既有 `.entity-list.density-compact` 紧凑规则（减少行内边距 `10px→6px`、字号与间隙，提升信息密度）。
- 复用既有 `listDensity` 信号 + `toggleListDensity()` 持久化逻辑，纯前端，**零后端契约变更**。

## Impact
- 仅前端模板/样式变更，不影响任何 API 契约或后端逻辑。
- 切换状态经 `localStorage['agentboard_list_density']` 持久化，刷新后保持。
- 新增 E2E `tests/test_epic66_v53_row_density_e2e.py` 覆盖：默认舒适、点击切紧凑（计算 padding `10px→6px`）、再点恢复、刷新持久化 + 0 控制台/页面/404 错误。

## Status
Implemented（in_review）
