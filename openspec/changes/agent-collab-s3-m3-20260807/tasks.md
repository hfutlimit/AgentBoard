# Tasks：多数决评审（S3 M3）

> ID: agent-collab-s3-m3-20260807 · Epic 122 / Story 232 / Task 1015

## 任务清单

- [x] **T1 迁移 q6r7s8t9u0v1**：`review_votes` 表（entity_type/entity_id/
  reviewer_user_id/verdict/comment_id/round + UNIQUE 一人一票 + 实体索引），
  双后端兼容，幂等（表已存在则跳过）。
- [x] **T2 模型**：`ReviewVote` 实体（domains/projects/models.py）+
  门面 models.py 导出。
- [x] **T3 mq.py**：`EVENT_REVIEW_VOTE_CAST = "review.vote_cast"` 进
  WORKFLOW_EVENTS 白名单。
- [x] **T4 service.py 多数决核心**：`get_review_mode` / `get_review_quorum` /
  `_is_reviewer_candidate` / `_upsert_review_vote` / `_review_vote_counts` /
  `_clear_review_votes` / `_settle_majority_approved` / `_settle_majority_rejected` /
  `_vote_majority`；`review_story` / `review_task` 加 majority 分支（默认 single 不变）。
- [x] **T5 service.py 超时兜底**：`scan_review_timeouts` majority 分支
  （票数>0 → 按现有票结算；零票 → 重派），result 加 stories/tasks_settled。
- [x] **T6 api.py 事件适配**：review 端点结算判定（ready/done → 既有事件；
  blocked / round 增加 → rejected；其余 → vote_cast，ref_id=投票人），
  Webhook 通道同步。
- [x] **T7 workflow_worker.py**：vote_cast 显式日志分支（避免未识别事件告警）。
- [x] **T8 测试** tests/test_epic122_s3m3.py：配置/通过/驳回/未达 quorum/
  一人一票/平局/超时兜底/single 兼容/权限/api 事件/Epic 97 AST 护栏。
- [x] **T9 回归**：epic122 全系 + crud_smoke 零失败。
- [x] **T10 部署验证**：docker restart api（alembic 自动升级 q6r7s8t9u0v1），
  REST 冒烟 + Playwright E2E 0 错误。
- [x] **T11 OpenSpec**：proposal / design / tasks 三件套。

## 验证记录

- pytest tests/test_epic122_s3m3.py：全部通过（见执行日志）；
- 回归：epic122 s1/s2/s3 全系 + crud_smoke 零失败；
- REST 冒烟：majority 模式 3 票 2 approve → Story ready；事件链路
  vote_cast → story.ready；
- Playwright E2E（28080）：登录/项目页/Story 页 0 console/pageerror；
- alembic：docker logs 确认 Running upgrade p5q6r7s8t9u0 → q6r7s8t9u0v1；
- 硬约束：未触碰 18001；零新增依赖；默认 single 行为零回归。
