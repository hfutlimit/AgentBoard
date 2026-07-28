# Design: 看板视图列内按维度子分组 (Epic 74 v6.1)

## 实现要点

### 1. 分桶方法 `boardSubGroups(status)`（app.ts）
- 输入：单个状态列 `status`。
- 取 `list = this.tasksForStatus(status)`（已是该状态下、经 `visibleTasks` 过滤后的卡片）。
- `taskGroupBy()==='none' | 'status'` → 返回 `[{key:'', items:list}]`（平铺，列内仅一类，退化当前行为）。
- 否则按与 `groupedTasks()` 完全一致的分桶键：
  - `type` → `t.type`
  - `priority` → `t.priority || 'medium'`
  - `due` → `this.dueBucket(t)`
  - `assignee` → `t.assignee_id ?? 'unassigned'`
- 键序复用既有常量：`['task','bug','test_execution']`（type）、`this.priorities`（priority）、`this.dueBucketOrder`（due）、assignee 按中文名 localeCompare。
- 每个子分组 `{key, label:this.groupLabel(g,k), count, items}`。
- 空列返回 `[]`（让模板 `@empty` 显示「暂无任务」）。

### 2. 模板结构（app.html 看板分支）
- `.kanban-col-body`（拖拽目标，绑定 `onKanbanDrop($event,s)`）**不变**。
- 其内部由「平铺 `@for (t of tasksForStatus(s))`」改为「`@for (grp of boardSubGroups(s))`」：
  - `@if (grp.key)` → 渲染 `.kanban-subgroup-header`（`.kanban-subgroup-label` + `.kanban-subgroup-count`）。
  - 卡片 `<article class="kanban-card ...">` 移到 `.kanban-subgroup-body` 内，逻辑零改动（拖拽/勾选/完成/快速查看全部保留）。
- `@empty` 仍挂在 `@for (grp ...)` 上：空列时 `boardSubGroups` 返回 `[]` → 显示「暂无任务」。

### 3. 样式（styles.css，主题感知）
- `.kanban-subgroup` / `.has-header`（间距）
- `.kanban-subgroup-header`：flex + sticky（`top:0; z-index:2; backdrop-filter:blur(6px)`），背景用 `color-mix(in srgb, var(--surface-2) 72%, transparent)`。
- `.kanban-subgroup-count`：胶囊徽章，复用 `--brand-soft` / `--brand-600`。
- `[data-theme="dark"] .kanban-subgroup-header` 调低 surface 占比。
- 全部基于既有的 `--surface-2/--border/--text-2/--brand-*` 变量，明暗自动适配。

## 验收
- 登录 admin → 种子 story（epic 64）→ 切看板。
- `不分组`：列内 1 个子分组、无头、卡片平铺。
- `按优先级`：列内 3 个子分组头（高/中/低），计数各 1，卡片归位；`.kanban-col-body` 拖拽目标仍在。
- `按类型`：3 头（Task/Bug/Test Execution）。
- 切回 `不分组`：退化平铺。
- 0 pageerror / console / .js+.css 404。
