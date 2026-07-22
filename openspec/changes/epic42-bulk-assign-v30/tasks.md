# Tasks: 任务列表批量指派 (v3.0 / Epic 42)

## 实现任务
- [x] 后端：`BulkTaskUpdate` 增加 `assignee_id` / `clear_assignee` 字段
- [x] 后端：`bulk_update_tasks` 处理 `assignee_id` / `clear_assignee`
- [x] 前端：`api.service.bulkUpdateTasks` 类型扩展
- [x] 前端：`app.ts` 新增 `bulkAssigneeId` 信号与 `bulkUpdateAssignee` 方法，`showBulkActionPanel` 扩 `'assignee'`
- [x] 前端：`app.html` 批量栏新增「批量指派」按钮与指派人面板
- [x] 前端：`app.css` 补 `.bulk-assignee-select` 样式
- [x] 构建并拷贝静态资源至 `agentboard/web/static/`
- [x] 重启本地 API（58125）加载新后端

## 验证任务
- [x] 后端 pytest：`tests/test_epic42_bulk_assign.py`（assignee_id / clear_assignee 双路径）
- [x] 前端 Playwright E2E：`tests/test_epic42_bulk_assign_e2e.py`（勾选→指派→断言→清除→零错误）
- [x] 回归：既有 bulk 状态/优先级/删除 E2E 与 cache pytest 无回归

## 状态流转（经 MCP）
- 新建 Epic 42（v3.0）→ Story → Task（high）→ backlog → todo → in_progress → in_review
