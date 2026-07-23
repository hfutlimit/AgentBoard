# Design: v3.3 排序维度增强

## 现状
`visibleTasks` computed 在 `frontend/src/app/app.ts` 已支持 `created_at / updated_at / priority / title / status`
五类排序键，下拉 `taskSortOptions` 与之对应。新增维度只需在「类型联合」「比较分支」「选项列表」三处扩展。

## 排序比较语义
复用既有统一收尾 `return sortOrder === 'asc' ? cmp : -cmp`，新增两个分支：

### due_date（截止日期）
```
cmp = this.compareDueDate(a.due_date, b.due_date);
```
- 两方均无日期 → 0（等序）
- 仅一方无日期 → 返回 `1`（升序时置于后；经外层取反后降序置于前，符合 SQL `ORDER BY due_date NULLS LAST/FIRST` 标准语义）
- 均有日期 → `new Date(a) - new Date(b)`（ISO 字符串可直接比较）

### assignee（指派人）
```
cmp = this.assigneeSortLabel(a).localeCompare(this.assigneeSortLabel(b));
```
- 未指派（`assignee_id == null`）→ 哨兵 `'\uFFFF'`，localeCompare 下恒排最后（升序）/最前（降序）
- 已指派 → 取 `getAssigneeName(assignee_id)` 显示名比较，空则回退 `u{id}`

## 持久化
`taskSortKey` / `taskSortOrder` 已通过 `localStorage` 读写，新增键 `due_date` / `assignee` 自动享受该机制，无需额外代码。

## 验证
端到端（Playwright）：自建 7 个受控任务（覆盖 due/assignee 交叉组合），断言
- 下拉含 7 项且含「截止日期」「指派人」
- 截止日期升序：有日期行按 ISO 单调不增且全部置前、无日期行全部置后
- 截止日期降序：反转（无日期置前）
- 指派人升序：已指派置前、未指派置后；降序反转
- 刷新后排序键与方向持久化
- 0 pageerror / console error / .js+.css 404
测试末清理自建任务、恢复默认排序，不污染数据。
