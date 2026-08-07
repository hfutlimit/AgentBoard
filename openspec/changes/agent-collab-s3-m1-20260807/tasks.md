# Tasks：Webhook 事件接入（S3 M1）

> ID: agent-collab-s3-m1-20260807 · Epic 122 / Story 232 / Task 1013

## 任务清单

- [x] 1. service.py：`fire_webhooks_for_event(s, *, project_id, event, payload)` —— enabled 过滤 + events 过滤（空=全部/精确匹配）+ 项目级或全局（project_id=NULL）命中 + 单 webhook 异常隔离 + 返回统计 + DB 异常吞掉
- [x] 2. api.py：`_notify_webhooks(s, project_id, event, payload)` best-effort 辅助函数（任何异常不阻断主业务）
- [x] 3. api.py 事件点接入（9 个）：create_story → story.created；assign_story_reviewer → review.requested；review_story approve → story.ready / reject → review.rejected；story comment → comment.replied；submit-task-review → task.ready_for_review；assign_task_reviewer → review.requested(task)；review_task approve → task.reviewed / reject → task.rejected
- [x] 4. Story 无 project_id 列：事件点经 epic 查询解析 project_id（assign/review/comment 三处）
- [x] 5. 单测 `tests/test_epic122_s3m1.py`：18 用例（过滤语义/全局 webhook/异常隔离/统计/API 接入点/best-effort/MCP AST 护栏）
- [x] 6. 修复既有测试缺陷：s2m1/s2m2 字符串路径 mock → `mock.patch.object`（模块重导入污染）；s2m2 assign flaky 固定 reviewer（seed 双 reviewer 随机指派 → 手动覆盖 rev1）
- [x] 7. 回归：epic122 s1/s2 + agent_review + epic97 + crud_smoke → 122 passed / 9 skipped 全绿
- [x] 8. E2E `tests/test_epic122_s3m1_e2e.py`：REST 事件点 + Playwright 3 页 0 console/pageerror/js·css
- [x] 9. 部署：docker restart api（bind mount 本地源码自动可见）；真实接收端验证 story.created / task.ready_for_review + HMAC 签名送达；不触碰 18001
- [x] 10. 状态流转：Task 1013 → in_review；Story 232 保持 in_progress

## 验证记录

- `pytest tests/test_epic122_s3m1.py` → 18 passed
- 回归（s3m1+s2m1+s2m2+s1_m3_worker_mcp+s1_m3_events+s1_m2_mq+agent_review+epic97+crud_smoke）→ 122 passed, 9 skipped
- E2E → REST 全链路 + Playwright 3 页 0 错误，ALL PASS
- 部署冒烟 → 本地接收端（127.0.0.1:9999）实收 `story.created`（218/219）与 `task.ready_for_review`（1072），`X-AgentBoard-Signature` HMAC-SHA256 正确
- 提交 7bb7a93，push 成功（bddafb2..7bb7a93）
