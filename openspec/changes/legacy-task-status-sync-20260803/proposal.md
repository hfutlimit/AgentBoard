# Change: 遗留任务状态同步验收 — Task 87/88/89/102 → in_review（2026-08-03）

## Why

自动开发巡检发现 4 个**历史遗留任务**：代码实现早已完成（对应 Story 27/28/32 均已 done），但任务状态长期停留在 `in_progress` / `backlog` 未收尾，导致 MCP 权威数据与真实交付状态不一致：

| Task | 标题 | 原状态 | 对应 Story |
|---|---|---|---|
| 87 | 任务详情附件区与 MCP 资源信息工具 | in_progress | S27 附件（done） |
| 88 | AgentSchedule / AgentRun 模型、一次性与 cron 表达式校验 | in_progress | S28 定时 Agent 开发（done） |
| 89 | 带租约和幂等键的调度扫描器，避免重复运行 | in_progress | S28（done） |
| 102 | MCP 工具补全（成员管理/通知/统计/管理员） | backlog | S32 管理员后台（done） |

## 验收依据（功能已实现且可用）

- **Task 88/89（调度）**：`agentboard/domains/scheduling/models.py`（AgentSchedule/AgentRun + cron CheckConstraint + idempotency_key 唯一约束）+ `agentboard/scheduler.py`（SELECT FOR UPDATE NOWAIT 租约 + idempotency_key 幂等）。`tests/test_scheduler.py` 11 passed。生产 MCP `create_schedule` / `delete_schedule` / `list_schedules` 实测可用。
- **Task 87（附件）**：`agentboard/api.py` 附件 CRUD（list/upload/download/info）+ `agentboard/mcp_server.py` 的 `list_attachments` / `get_attachment_info` 工具；前端 `app.html` 任务详情附件区（附件列表 + 下载）。生产 MCP 工具实测返回正常。
- **Task 102（MCP 工具补全）**：`mcp_server.py` 中 `list_members` / `add_member` / `remove_member` / `update_member_role` / `list_notifications` / `notification_unread_count` / `mark_notification_read` / `mark_all_notifications_read` / `delete_notification` / `get_project_stats` / `admin_list_users` / `admin_set_user_admin` / `admin_list_projects` / `admin_delete_project` 全部注册。生产 MCP 实测：list_members / get_project_stats / list_notifications / admin_list_users 均正常返回。
- **回归**：`test_scheduler` + `test_domain_boundaries` + `test_crud_smoke` + `test_api_keys` + `test_admin_api_key_scope` = 16 passed / 9 skipped / 0 failed。

## 状态变更（经生产 MCP 权威源执行）

- Task 87 / 88 / 89：`in_progress → in_review`（合法迁移）
- Task 102：`backlog → todo → in_progress → in_review`（逐级迁移；生产版本状态机不支持跨级跳转）

## 结论

4 个遗留任务全部推进至 `in_review`，MCP 权威数据与交付状态一致。Story 27/28/32 维持 done 不变。
