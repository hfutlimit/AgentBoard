# Design: 任务列表批量指派 (v3.0 / Epic 42)

## 后端（增量，向后兼容）
文件：`agentboard/api.py`
- `BulkTaskUpdate` 模型新增两个可选字段：
  - `assignee_id: int | None = None`
  - `clear_assignee: bool = False`
- `bulk_update_tasks` 中在组装 `updates` 字典时增加分支：
  ```python
  if body.assignee_id is not None:
      updates["assignee_id"] = body.assignee_id
  elif body.clear_assignee:
      updates["assignee_id"] = None
  ```
- 命中 `service.update_task(s, tid, **updates)`，`assignee_id` 走既有分支
  （service.py `update_task` 已支持 `assignee_id`，含成员存在性校验与 `task_assigned` 通知）。
- 设计要点：用 `clear_assignee` 哨兵区分「未传」与「显式取消指派」，
  因为 Pydantic 模型 `assignee_id` 默认值 `None` 无法区分二者。

## 前端
文件：`frontend/src/app/`
- `api.service.ts`：`bulkUpdateTasks` 的 `updates` 类型扩展为
  `{ status?; priority?; sprint_id?; assignee_id?; clear_assignee? }`。
- `app.ts`：
  - 新增信号 `bulkAssigneeId = signal<number | null>(null)`（当前选中的指派人）。
  - 新增 `bulkUpdateAssignee(newAssigneeId: number | null)` 方法，
    镜像 `bulkUpdatePriority`：组装 `{ assignee_id }` 或 `{ clear_assignee: true }`、
    复用 `bulkProgress` / `notify` / `clearTaskSelection` / `refresh`。
  - `showBulkActionPanel(type)` 类型扩展 `'assignee'`。
- `app.html`：批量操作栏新增「批量指派」按钮；新增 `bulkActionTarget()==='assignee'`
  面板，内含 `<select>`（成员列表 + 未指派）与「应用 / 取消」。
- `app.css`：新增 `.bulk-assignee-select` 样式（沿用 `--card-bg/--border/--text` 主题变量）。

## 数据流
勾选任务 → 点「批量指派」→ 选成员/未指派 → 应用 →
`POST /api/tasks/bulk-update {task_ids, assignee_id|clear_assignee}` →
后端逐任务 `update_task` → 前端 `refresh()` 重渲染列表与指派人头像。

## 验证
- 后端 pytest：直接调用 `bulk_update_tasks`（构造 `BulkTaskUpdate` 含 `assignee_id` / `clear_assignee`）。
- 前端 Playwright E2E：登录 admin → 进入含多任务的 Story → 勾选若干任务 →
  点「批量指派」→ 选某成员 → 应用 → 断言列表对应任务指派人已更新 → 再次验证「未指派」清除 →
  全程 0 pageerror / console / .js+.css 404。
