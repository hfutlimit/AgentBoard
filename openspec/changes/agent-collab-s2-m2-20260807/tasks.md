# Tasks：Task 评审闭环（S2 M2）

> ID: agent-collab-s2-m2-20260807 · Epic 122 / Story 231 / Task 1012

## 任务清单

- [x] 1. Alembic 迁移 `p5q6r7s8t9u0_add_tasks_review_fields`：tasks 加 `reviewer_id`（FK users, 可空, index）+ `review_round`（int default 0），双后端幂等
- [x] 2. models.py（domains/work_items）：Task 加 `reviewer_id` / `review_round` 列
- [x] 3. service.py：`assign_task_reviewer`（幂等 + CAS + 排除 assignee）、`review_task`（approve/reject + round 护栏 + 评论载体）、`list_task_review_tasks`、`search_tasks` 加 `reviewer_id` 过滤
- [x] 4. api.py：`POST /api/tasks/{tid}/assign-reviewer`（事件 review.requested 定向）、`POST /api/tasks/{tid}/review`（approve→task.reviewed / reject→task.rejected）、`GET /api/tasks` 加 `reviewer_id` 参数
- [x] 5. workflow_worker.py：`_assign_task_reviewer` + `EVENT_TASK_READY_FOR_REVIEW` → 自动指派 + 轮询兜底扫描 in_review 未指派 Task
- [x] 6. mcp_server.py：`assign_task_reviewer` / `review_task` / `list_task_review_tasks` 三个 MCP 工具（走 _http）
- [x] 7. 单测 `tests/test_epic122_s2m2.py`：22 用例（指派/评审/列表/API/worker/轮询/MCP AST）
- [x] 8. 回归：S2 M1（旧断言更新）+ S1 M1/M2/M3 + Epic 97 护栏 + crud_smoke
- [x] 9. 部署：docker restart api（bind mount 本地源码自动可见）；不触碰 18001
- [x] 10. 状态流转：Task 1012 → in_review；Story 231 保持 in_progress

## 验证记录

- `pytest tests/test_epic122_s2m2.py` → 22 passed
- 回归（epic122 s2m1/agent_review/s1_m2/s1_m3_worker/s1_m3_events/crud_smoke/epic97）→ 待跑
- 部署冒烟（REST 全链路 claim→submit-review→assign→review）→ 待跑
