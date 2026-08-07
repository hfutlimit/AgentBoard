# Tasks：评审统计与超时护栏（S3 M2）

> ID: agent-collab-s3-m2-20260807 · Epic 122 / Story 232 / Task 1014

## 任务清单

- [ ] T1 service.get_review_stats：项目级评审统计（stories/tasks 汇总 + rounds +
      reject_rate + timeout_pending + by_reviewer），days/user_id 过滤
- [ ] T2 service.scan_review_timeouts：超时判定（Story 用评论时间兜底 / Task 用
      updated_at）+ CAS 解绑重派 + 轮次上限 blocked + no_candidate 兜底 +
      max_per_run 有界
- [ ] T3 api：GET /api/review-stats + POST /api/review-stats/reassign-timeout
      （事件发布 review.requested 定向退广播 + Webhook 通道）
- [ ] T4 workflow_worker.run_poll_once 追加超时重派扫描（best-effort）
- [ ] T5 mcp_server：get_review_stats / scan_review_timeouts 两工具（_http）
- [ ] T6 tests/test_epic122_s3m2.py 全量单测（统计口径 / 超时重派 / API 权限与
      事件 / MCP AST + 直调 / Epic 97 护栏）
- [ ] T7 回归：既有 epic122 全系列 + crud_smoke 零失败
- [ ] T8 E2E：Playwright 冒烟（REST 全链路 + 页面 0 错误）

## 依赖

- S3 M1（Task 1013）：Webhook 事件接入，`_notify_webhooks` 可用
- S1/S2：`assign_reviewer` / `assign_task_reviewer` / `MAX_REVIEW_ROUNDS` /
  `_online_reviewer_candidates` 可复用

## 验收

- T1-T5 全部实现并通过 T6 单测；
- T7 既有测试零回归；T8 E2E 无 console/pageerror/js-css 失败；
- 零新增第三方依赖；不触碰 18001；MCP 状态：Task 1014 → in_review。
