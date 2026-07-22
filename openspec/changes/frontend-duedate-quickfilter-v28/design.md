# Design: 任务列表截止日期快速筛选 chips (v2.8 / Epic 40)

## 分桶模型（mutually exclusive）
以「本地日期零点」为基准计算与 `due_date` 的天数差 `diff`：
- `overdue`：`diff < 0` 且 `status !== 'done'`（逾期未完成）
- `today`：`diff === 0`
- `week`：`1 <= diff <= 7`（未来 7 天内）
- `later`：`diff > 7`（不暴露为 chip，归入「全部」）
- `none`：`due_date` 为空/非法

> 注：`overdue` 桶在计数与过滤时均排除已完成任务，与既有 `filterOnlyOverdue` 语义一致。

## 状态与信号
- 新增 `filterDueDate: signal<string>`（`''`=全部，`'overdue'|'today'|'week'|'none'`），
  初始化读 `localStorage['agentboard_quick_due']`，替换原 `filterOnlyOverdue` 信号。
- `dueCounts: computed<Record<string,number>>`：基于当前 story 全量 `tasks()` 统计 4 个分桶计数（不受筛选影响）。
- `dueBucket(t): private`：将任务归入上述分桶。
- `setQuickDue(d)`：单选切换 + 持久化（与 `setQuickPriority/Status/Type/Assignee` 对称）。

## 过滤接入
`visibleTasks` 内原 `filterOnlyOverdue` 分支替换为：
```
const fd = this.filterDueDate();
if (fd) {
  const b = this.dueBucket(t);
  const overdueDone = b === 'overdue' && t.status === 'done';
  if (overdueDone || b !== fd) return false;
}
```

## 联动
- `activeFilterCount`：`filterOnlyOverdue` → `filterDueDate` 计数。
- `clearFilters()` / `clearAllFilters()`：重置 `filterDueDate=''` 并清理 `localStorage`。
- 高级筛选面板「仅看逾期」勾选框：`[checked]="filterDueDate()==='overdue'"` + `(change)="setQuickDue(...)"`。

## UI / 样式
- `app.html`：指派人 chips 之后新增第 5 个 `.task-quickfilter-bar`，复用 `.qf-chip`/`.qf-count`。
- `app.css`：新增 `.qf-due-icon`（图标间距）+ `.qf-chip.active.qf-due`（橙→红渐变高亮，区分于其它 chip）。

## 变更文件
- `frontend/src/app/app.ts`：信号 / computed / 方法 / `visibleTasks` 分支 / 清理联动
- `frontend/src/app/app.html`：第 5 个筛选条 + 高级面板 overdue 复用
- `frontend/src/app/app.css`：`.qf-due-icon` / `.qf-chip.active.qf-due`
