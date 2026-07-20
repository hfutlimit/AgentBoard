# Design: 状态快速筛选 chips (v2.5 / Epic 37)

## 现有可复用资产
- `filterStatus = signal('')` 已声明（`app.ts` Task 602 区块），并在 `visibleTasks` 中已生效：`if (status && t.status !== status) return false;`
- 优先级 chips 已落地 v2.0（`setQuickPriority` / `priorityCounts` / `agentboard_quick_priority` 持久化），可作为实现模板
- `statuses: Status[]` 数组（backlog/todo/in_progress/in_review/verifying/done）已存在
- 既有 `statusLabel(status)` 方法（待规划/待办/进行中/评审中/验证中/完成）已全局使用，直接复用，**不再新增同名方法**

## 方案
1. **信号初始化**：`filterStatus` 初始化时从 `localStorage['agentboard_quick_status']` 读取（与优先级 chips 对称）
2. **计数**：新增 `statusCounts` computed，遍历 `this.tasks()` 统计各状态数量（不受筛选影响）
3. **交互**：新增 `setQuickStatus(s)` 单选切换 + `persistQuickStatus()` 持久化；再次点击同状态取消
4. **清除联动**：`clearFilters()` 增加 `filterStatus.set('')` 与 `persistQuickStatus()`；`activeFilterCount` 纳入 `filterStatus()` 使「清除全部筛选」在选中状态时显隐
5. **UI**：在优先级 chips 之后追加第二个 `.task-quickfilter-bar`，渲染「全部」+ 各状态 chip（含 `statusColor(s)` 色点）
6. **样式**：复用 `.qf-chip`/`.qf-count`，新增 `.qf-dot` 8px 圆形色点

## 状态机注意
任务状态变更须走合法迁移（BACKLOG→TODO→IN_PROGRESS→IN_REVIEW…），无 `backlog→in_review` 直达边。本功能仅做客户端过滤，不涉及状态迁移。
