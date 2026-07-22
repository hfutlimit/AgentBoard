# Tasks: 任务列表截止日期快速筛选 chips (v2.8 / Epic 40)

## Task 1（high）：实现截止日期快速筛选 chips
- [x] `app.ts`：新增 `filterDueDate` 信号（替换 `filterOnlyOverdue`）+ `dueCounts` computed + `dueBucket()` 私有方法 + `setQuickDue()` 单选切换与持久化
- [x] `app.ts`：`visibleTasks` 过滤分支接入 `filterDueDate` 分桶匹配（保留「逾期排除已完成」语义）
- [x] `app.ts`：`activeFilterCount` / `clearFilters` / `clearAllFilters` 联动重置 `filterDueDate`
- [x] `app.html`：指派人 chips 后新增第 5 个 `.task-quickfilter-bar`（全部/逾期/今天/本周/无截止 + 计数）
- [x] `app.html`：高级筛选面板「仅看逾期」勾选框复用 `filterDueDate('overdue')`
- [x] `app.css`：`.qf-due-icon` 图标间距 + `.qf-chip.active.qf-due` 橙红渐变高亮
- [x] `npm run build` 通过，`cp` 至 `agentboard/web/static/` 即时生效

## Task 2（medium）：端到端验证与回归
- [x] Playwright E2E `tests/test_epic40_v28_due_quickfilter_e2e.py`：登录 → story 25 → 截止日期 chips 渲染/计数/单选/持久化/reload，0 pageerror/console/.js+.css 404
- [x] 回归：既有 pytest（cache）+ v2.0~v2.7 E2E 全绿
- [x] 后端无需改动（纯前端）

## 验收标准
- 工具条渲染 5 个分桶 chip + 实时计数
- 点「逾期」→ 列表仅剩未完成且逾期的任务；点「本周」→ 未来 7 天内到期；点「无截止」→ 无 due_date
- 选中后刷新页面偏好保留（localStorage `agentboard_quick_due`）
- 「清除全部筛选」可一键重置；高级面板「仅看逾期」勾选与 chip 状态双向同步
- 主流程与既有 chips 无回归，控制台/网络 0 错误
