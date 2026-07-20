# 设计：前端体验升级 v2.4 — 任务类型快速筛选 chips

## 复用与边界（纪律）
- **复用**：`filterTypes` 信号（L327）、`visibleTasks()` 内 `filterTypes` 过滤（L376-377）、`activeFilterCount`（L338，已计入 `filterTypes().length`）、`clearFilters()`（L3072）。
- **新增**：`typeCounts` computed（与 `priorityCounts`/`statusCounts` 同构，基于 `this.tasks()` 全量计数，不受筛选影响）；`setQuickType(t)` 单选切换（与 `setQuickPriority`/`setQuickStatus` 同构）；`persistQuickType()` 持久化。
- **初始化**：`filterTypes` 初始值从 `localStorage.agentboard_quick_type` 解析，与优先级/状态一致。
- **不触碰**：`models.py` / `api.py` / 数据模型 / docker / 高级筛选面板的 `toggleFilterType`（多选自选入口保留，两者写同一信号并存）。

## 交互细节
- 工具条结构（顺序）：搜索框 → 优先级 chips → 状态 chips → **类型 chips（新增）** → 只看我 → 排序 → 分组 → 清除全部筛选。
- 类型枚举固定 `['task','bug']`；标签：`task→任务`、`bug→Bug`（与 `groupLabel`/高级面板一致）。
- 单选语义：`setQuickType('')` 清空；`setQuickType(t)` 当已选 `t` 则清空，否则置 `[t]`（与优先级/状态 chips 完全对齐）。
- 计数：「全部」显示 `tasks().length`（当前 story 全量）；各类型显示 `typeCounts()[t]`。
- 持久化键：`agentboard_quick_type`（JSON 数组）；`clearFilters()` 额外调用 `persistQuickType()` 与移除存储。

## 验收
- 类型 chips 渲染为 3 枚（`全部`+`任务`+`Bug`）；计数与列表一致。
- 点「Bug」→ 列表仅含 `type==='bug'`；点「任务」→ 仅 `task`；点「全部」→ 还原。
- reload 后选择保留（localStorage）。
- 与优先级/状态 chips 组合无冲突（多重筛选叠加生效）。
- Playwright E2E 0 pageerror / console / .js+.css 404。
