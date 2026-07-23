# Tasks: v3.3 排序维度增强

## Task 1 — 扩展排序类型与比较分支
- [x] `taskSortKey` 联合类型增加 `'due_date' | 'assignee'`
- [x] `visibleTasks` 排序 `filtered.sort` 增加 `due_date` / `assignee` 两个 `else if` 分支
- [x] 新增私有方法 `compareDueDate(da, db)` 与 `assigneeSortLabel(t)`

## Task 2 — 下拉选项曝光
- [x] `taskSortOptions` 增加 `{ key:'due_date', label:'截止日期' }` 与 `{ key:'assignee', label:'指派人' }`
- [x] 现有 `<select>` 通过 `@for (opt of taskSortOptions)` 自动渲染，无需改模板

## Task 3 — 构建与部署
- [x] `npm run build`（managed Node 22.22.2，清空 `.angular/cache`）
- [x] 产物 `main-GEAJLC5P.js` 拷贝至 `agentboard/web/static/`，删除旧 `main-45AUETER.js`

## Task 4 — 端到端验证
- [x] 新增 `tests/test_epic46_v33_sort_dims_e2e.py`：自建/清理受控任务 + 4 组排序不变量断言 + 持久化 + 0 错误
- [x] 回归：`pytest tests/test_epic30_cache.py`（7 passed/1 skipped）、`tests/test_epic39_v26_status_sort_e2e.py`（ALL PASS）无回归

## Task 5 — 状态流转（MCP/REST 兜底）
- [x] REST 新建 tracker project 108 / epic 116 / story 183 / task 895（high）
- [x] 合法链 backlog→todo→in_progress→in_review；story 183、epic 116 同步 in_review
