# Tasks: 按优先级分组 (Epic 49 v3.6)

## 实现任务
- [x] **T1** `app.ts`：`taskGroupBy` 联合类型增加 `'priority'`；`taskGroupOptions` 增加「按优先级」选项。
- [x] **T2** `app.ts`：`groupedTasks` 增加 `priority` 分桶逻辑（键 `t.priority || 'medium'`，键序用 `this.priorities` 过滤）。
- [x] **T3** `app.ts`：`groupLabel` 增加 `priority` 分支，复用 `priorityLabel()`。
- [x] **T4** `app.html`：分组头新增优先级色徽章 `<span class="badge priority priority--{{grp.key}}">`。
- [x] **T5** 构建：`npm run build`（managed node 22.22.2，清 `.angular/cache`）→ cp 至 `agentboard/web/static/`。
- [x] **T6** 验证：Playwright E2E `tests/test_epic49_v36_priority_group_e2e.py` 全绿。
- [x] **T7** 回归：`pytest test_epic30_cache.py` + v3.5/v3.4 E2E 全绿，无回归。

## 验收结论
- 分组下拉新增「按优先级」；选中使用 highest→lowest 顺序渲染分组；分组头带色徽章与计数。
- 0 pageerror / console error / .js+.css 404。
- 追踪：task 969 / story 187 / epic 120 均置 **in_review**。
