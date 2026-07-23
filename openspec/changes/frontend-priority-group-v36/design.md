# Design: 按优先级分组

## 数据来源
分组完全基于前端 `visibleTasks()` 计算，与既有 `groupedTasks` computed 合流，零后端契约变更。

## 实现要点
1. **维度扩展**：`taskGroupBy` signal 联合类型增加 `'priority'`；`taskGroupOptions` 增加
   `{ key: 'priority', label: '按优先级' }`（位于「按类型」之后、「按负责人」之前，保持语义接近的分组相邻）。
2. **分桶**：`groupedTasks` 中 `g === 'priority'` 时取 `t.priority || 'medium'` 作为桶键；
   键序使用既有 `this.priorities`（`['highest','high','medium','low','lowest']`）过滤出存在的桶，
   保证分组头顺序恒为「高→低」，与用户心智一致。
3. **文案**：`groupLabel` 新增 `mode === 'priority'` 分支，复用既有 `priorityLabel()`。
4. **视觉**：分组头在 `.task-group-label` 后新增 `<span class="badge priority priority--{{grp.key}}">`，
   复用任务行已有的 `.priority--*` 配色，无需新增 CSS。

## 状态流转
- 追踪实体通过 REST 在运行时库创建：project 112 (AUTODEV49) → epic 120 → story 187 → task 969。
- 状态机合法链 `backlog→todo→in_progress→in_review`（PUT /api/tasks/{tid}/status），
  story/epic 经 PATCH 同步 `in_review`。

## 验证
- Playwright E2E `tests/test_epic49_v36_priority_group_e2e.py`：选「按优先级」→ 3 组（high/medium/low）、
  顺序 high→medium→low、各组带 `priority--{x}` 徽章且文案为 高/中/低、计数和==任务总数、0 报错。
- 回归：`pytest test_epic30_cache.py`（7 passed/1 skipped）、v3.5/v3.4 E2E 全绿。
