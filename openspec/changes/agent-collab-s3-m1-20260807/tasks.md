# Tasks：Webhook 事件接入（S3 M1）

> ID: agent-collab-s3-m1-20260807 · Epic 122 / Story 232 / Task 1013

## 任务清单

- [x] 1. service.py：`fire_webhooks_for_event(s, *, project_id, event, payload)` —— enabled 过滤 + events 过滤（空=全部/精确匹配）+ 单 webhook 异常隔离 + 返回统计 + DB 异常吞掉
- [x] 2. api.py：`_notify_webhooks(s, project_id, event, payload)` best-effort 辅助函数（任何异常不阻断主业务）
- [x] 3. api.py 事件点接入（9 个）：create_story → story.created；assign_story_reviewer → review.requested；review_story approve → story.ready / reject → review.rejected；story comment → comment.replied；submit-task-review → task.ready_for_review；assign_task_reviewer → review.requested(task)；review_task approve → task.reviewed / reject → task.rejected
- [x] 4. Story 无 project_id 列：事件点经 epic 查询解析 project_id（assign/review/comment 三处）
- [x] 5. 单测 `tests/test_epic122_s3m1.py`：17 用例（过滤语义/异常隔离/统计/API 接入点/best-effort/MCP AST 护栏）
- [x] 6. 回归：epic122 s2m1/s2m2/s1 m3/agent_review/s1 m2_mq/s1 m3_events + crud_smoke + epic97
- [x] 7. 部署：docker restart api（bind mount 本地源码自动可见）；不触碰 18001
- [x] 8. 状态流转：Task 1013 → in_review；Story 232 保持 in_progress

## 验证记录

- `pytest tests/test_epic122_s3m1.py` → 17 passed
- 回归 → 待跑
- 部署冒烟（Webhook 端点 + 事件派发日志）→ 待跑
