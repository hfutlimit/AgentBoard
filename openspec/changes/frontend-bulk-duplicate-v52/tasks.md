# Tasks: 任务列表批量复制选中任务（克隆）(v5.2 / Epic 65)

## 实施任务
- [x] 新建追踪实体：project 52(AUTODEV65) → epic 55(Epic 65 v5.2) → story 104(Story 65.1) → task 1120(high)，状态机 `backlog→todo→in_progress→in_review`
- [x] `app.ts` 实现 `bulkDuplicate()`：遍历 `selectedTasks()` 调 `api.createTask` 克隆到各自 Story，含 `bulkProgress` 进度、`notify` 提示、`refresh()` 刷新
- [x] `app.html` 批量操作栏新增「批量复制」按钮（`(click)="bulkDuplicate()"`）
- [x] 构建（`npm run build`，managed node 22.22.2，清 `.angular/cache`）→ cp `dist/frontend/browser/.` → `agentboard/web/static/`（新 `main-NKG5CKRY.js`）
- [x] Playwright E2E `tests/test_epic65_v52_bulk_duplicate_e2e.py`：种子/清理自洽，断言克隆数与 toast，0 报错
- [x] 回归：`pytest test_epic30_cache.py` + v5.1/v4.x 相关 E2E
- [x] 提交 `feat(ui): 前端小优化 - 任务列表批量复制选中任务 (Epic 65 v5.2)` + push origin main

## 验收标准
- 多选任务后「批量复制」按钮可用；点击后每个选中任务在其所属 Story 生成 `(副本)` 克隆
- 完成后选择清空、列表刷新、toast 提示正确数量
- 零控制台/页面/.js+.css 404 错误
- 既有批量操作（状态/优先级/指派/截止/删除）无回归
