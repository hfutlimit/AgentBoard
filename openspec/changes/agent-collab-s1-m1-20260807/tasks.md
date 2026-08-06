# Tasks：S1 M1 实现清单

> ID: agent-collab-s1-m1-20260807 · 全部 `[x]` 表示已交付

## 实施步骤

- [x] **迁移**：`migrations/versions/o5p6q7r8s9t0_add_agents_story_review.py` — agents 表 + stories.reviewer_id/review_round + CHECK 扩展（SQLite batch / MariaDB drop+create）
- [x] **模型**：`domains/projects/models.py` — Agent 实体 + Story 新列 + `STORY_REVIEW_STATUSES`/`STORY_STATUS_SQL`；facade `models.py` 导出
- [x] **服务层**：`service.py` — `register_agent`/`get_agent_by_agent_id`/`agent_heartbeat`/`agent_deregister`/`list_agents`/`_online_reviewer_candidates`/`assign_reviewer`（CAS 幂等）/`review_story`（approve/reject + 评论 + round 护栏）/`list_review_tasks`；`update_story` 状态校验扩展
- [x] **API**：`api.py` — AgentRegisterIn/AgentReviewIn schema + 7 端点（register/heartbeat/deregister/list、assign-reviewer/review、全局 GET /api/stories）
- [x] **前端**：`app.ts` — statusLabel/statusColor/statusSemanticClass 补 pending_review/ready
- [x] **测试**：`tests/test_epic122_agent_review.py`（14 用例：注册幂等/过滤/心跳/归属/CAS/round 护栏/API 直调/状态不污染）
- [x] **回归**：test_epic118 + test_epic119 + test_crud_smoke 通过（17 passed / 9 skipped）

## 验证记录

- pytest 14 passed（test_epic122_agent_review.py）
- 前端构建 + Playwright E2E 回归（见执行日志）

## 状态

Task 1006 → in_review；Story 230 / Epic 122 保持 backlog→in_progress 视推进情况。
