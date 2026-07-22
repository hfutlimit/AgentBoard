# 设计：任务列表筛选预设（Epic 43 v3.1）

## 状态模型
既有筛选信号（单值为主）：
- `filterStatus: signal<string>`（''=全部）
- `filterPriorities / filterTypes / filterAssignees: signal<string[]>`（chips 单选，实际为 [] 或 [单值]）
- `filterDueDate: signal<string>`（''=全部 / overdue / today / week / none）
- `taskSearchQuery: signal<string>`
- `filterMineOnly: signal<boolean>`

预设即上述信号当前值的快照：
```ts
interface FilterPreset {
  name: string;
  status: string; priority: string; type: string;
  assignee: string; due: string;
  search: string; mineOnly: boolean;
}
```

## 数据流
1. **保存**：`saveFilterPreset()` 读取各信号当前值 → 构造 `FilterPreset` → 追加进 `filterPresets()` → `localStorage` 持久化。
2. **应用**：`applyFilterPreset(idx)` 先 `clearAllFilters()` 归零所有筛选 → 按预设逐项 `setQuick*()` / `taskSearchQuery.set()` / `filterMineOnly.set()` 还原 → 关闭面板。
3. **删除**：`deleteFilterPreset(idx)` 从数组中剔除 → 持久化。

## 关键设计点
- **复用现有 setter 而非直接写信号**：`setQuickStatus/setQuickPriority/setQuickType/setQuickAssignee/setQuickDue` 已封装持久化与单选语义，`apply` 复用它们可避免状态不一致、并自动写入各自 `localStorage`。
- **`clearAllFilters()` 已重置 `filterMineOnly`**：故 apply 时无需额外清 `mineOnly`，直接按预设 set 即可。
- **UI 收口**：预设控件挂在 `task-toolbar-secondary` 内，与「清除筛选」并列；面板用绝对定位浮层，复用 `.ghost-sm` / `.btn-primary-sm` 视觉体系，组件作用域 CSS（`app.css`）。

## 持久化
- key：`agentboard_filter_presets`（JSON 数组）。
- 读写包 try/catch，解析失败回退空数组，不影响主流程。

## 风险
- 极低：纯前端新增功能，未触碰任何既有筛选链路；`apply` 先 clear 再 set，状态可预测。
