# 设计：任务列表优先级快速筛选 chips

## 数据流
```
tasks (signal, 当前 story 全量)
  ├─ priorityCounts (computed) ──► 各 chip 的计数徽标
  └─ visibleTasks (computed) ──► 列表渲染
        └─ filterPriorities() 参与过滤

filterPriorities (signal<string[]>)
  ├─ init: 读取 localStorage.agentboard_quick_priority
  ├─ setQuickPriority(p): 单选切换（'' 或已选 → []；否则 → [p]）
  ├─ toggleFilterPriority(p): 多选切换（高级面板复用）
  ├─ clearFilters(): 清空（高级面板复用）
  └─ persistQuickPriority(): 写入 localStorage（上述三处调用）
```

## 组件改造
### app.ts
- `filterPriorities` 初始化时从 `localStorage.agentboard_quick_priority` 读取（JSON 解析，异常回退 `[]`）。
- 新增 `priorityCounts = computed<Record<string,number>>`：遍历 `tasks()` 统计 `highest/high/medium/low/lowest`。
- 新增 `setQuickPriority(p: string)`：单选语义；调用 `persistQuickPriority()`。
- 新增私有 `persistQuickPriority()`：写 `localStorage.agentboard_quick_priority`。
- `toggleFilterPriority` / `clearFilters` 末尾调用 `persistQuickPriority()`，保证高级面板操作同样持久化。

### app.html（Story 任务列表工具条，搜索框之后）
```
<div class="task-quickfilter-bar" role="group" aria-label="按优先级快速筛选">
  <button class="qf-chip" [class.active]="filterPriorities().length === 0" (click)="setQuickPriority('')">
    全部 <span class="qf-count">{{ tasks().length }}</span>
  </button>
  @for (p of priorities; track p) {
    <button class="qf-chip" [class.active]="filterPriorities().includes(p)" (click)="setQuickPriority(p)">
      {{ priorityLabel(p) }} <span class="qf-count">{{ priorityCounts()[p] }}</span>
    </button>
  }
</div>
```

### app.css
- `.task-quickfilter-bar`：flex 换行容器。
- `.qf-chip`：pill 样式，hover 微抬升；`.qf-chip.active`：品牌色填充 + 阴影。
- `.qf-count`：小号计数徽标，激活态反白。

## 主题适配
全部颜色使用 `var(--brand)` / `var(--text)` / `var(--muted)` 等既有 CSS 变量，自动适配 light/dark。

## 验收对照
| 验收项 | 实现 |
| --- | --- |
| 点击按优先级筛选 | `setQuickPriority` → `filterPriorities` → `visibleTasks` 过滤 |
| 计数正确 | `priorityCounts` 基于 `tasks()` 统计 |
| 刷新保留 | `init` 读 localStorage + `persistQuickPriority` 写入 |
| 与高级面板一致 | 共享 `filterPriorities` 信号 |
