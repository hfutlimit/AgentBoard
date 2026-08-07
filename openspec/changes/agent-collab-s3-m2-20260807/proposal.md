# Change Proposal：多 Agent 协作闭环 S3 M2（评审统计 + 超时护栏）

> ID: agent-collab-s3-m2-20260807 · Epic 122 / Story 232 / Task 1014

## 问题

Epic 122 已交付 S1（Agent 注册 + Story 评审闭环）、S2（Task 评审闭环）、S3 M1
（Webhook 事件接入）。但评审闭环仍缺**运营可观测性**与**超时自愈**：

1. **评审统计缺失**：无从得知每个 reviewer 的评审工作量、Story/Task 评审通过率、
   平均轮次、驳回率 —— 运营视图空白，Agent 协作质量无法度量；
2. **评审超时无护栏**：reviewer 失联/挂起后，`pending_review` Story 与 `in_review`
   Task 会**永久卡死** —— 只有 5 轮上限护栏（`MAX_REVIEW_ROUNDS`），没有「超时
   重派」机制。文档 #50 §8 切片 3 明确列出「评审统计与运营视图、护栏调优
   （轮次/超时/多数决）」为待交付项；轮次护栏已实现，超时重派缺失，多数决
   改动大（需评审记录表 + 状态机扩展）留后续。

## 目标

1. **评审统计运营视图**：`service.get_review_stats` —— 项目级 Story/Task 评审
   汇总（总量/approved/rejected/pending/blocked）、平均轮次、驳回率、当前超时
   未决数、按 reviewer 聚合工作量；`days` / `user_id` 过滤；
2. **超时重派护栏**：`service.scan_review_timeouts` —— 扫描「pending_review Story /
   in_review Task + reviewer 已指派 + 最后活动超时」的条目：轮次已达上限 →
   `blocked`（护栏终态）；否则解绑（CAS）→ 重新随机指派；无在线候选 → 解绑
   等待下轮轮询；
3. **REST 端点**：`GET /api/review-stats`（项目成员可读）+ `POST /api/review-stats/
   reassign-timeout`（触发扫描，重派成功发布 `review.requested` 事件 + Webhook
   通道）；`workflow_worker` 轮询自动触发；
4. **MCP 工具**：`get_review_stats` / `scan_review_timeouts` 暴露给 Agent。

## 非目标

多数决评审（需评审记录表 + N 人评审状态机，改动面大）、评审 SLA 报表导出、
超时阈值按实体粒度配置 —— 属后续迭代。

## 关键设计

- **超时定义（零迁移）**：Story 无 `updated_at` 列 → 「最后活动」= max(created_at,
  最新评论时间)（评审意见唯一载体是评论，评论往返即活动）；Task 有 `updated_at`
  直接使用；阈值默认 30 分钟，可参数化；
- **重派动作序列**（并发安全）：CAS 解绑 `UPDATE ... SET reviewer_id=NULL WHERE
  id=? AND reviewer_id=<old>`（rowcount 仲裁，防两 worker 同时重派）→ 重新随机
  指派（复用 `assign_reviewer` / `assign_task_reviewer` 候选集与 CAS）；无候选 →
  保持解绑由下轮轮询补派；
- **统计口径**：approved = Story ready / Task done（且 reviewer 已指派）；rejected =
  review_round > 0（评论往返产生过驳回）；pending = 评审进行中状态；blocked =
  轮次超限终态；reject_rate = rejected / (approved + rejected)；by_reviewer 按
  reviewer_id 聚合 reviewed/approve/reject 分布；
- **事件语义统一**：重派成功发布 `review.requested`（复用既有事件名与定向逻辑，
  reviewer agent 队列定向退广播），Webhook 通道并行，外部系统可订阅重派动作。

## 验收

1. pytest `test_epic122_s3m2.py`：统计口径（空项目全零 / 有数据 / days 过滤 /
   user_id 过滤 / reject_rate 与平均轮次正确）；超时重派（换 reviewer / 轮次上限
   blocked / 未超时不处理 / 无候选解绑 / Task 用 updated_at / max_per_run 有界）；
   API 权限与事件断言；MCP AST 注册 + 真实栈直调；Epic 97 AST 护栏零 `_api(`；
2. 既有测试零回归（epic122 s1/s2/s3m1 + agent_review + crud_smoke）；
3. 零新增第三方依赖；不触碰 18001。

## 关联

- 前置：S3 M1（Task 1013，Webhook 事件接入，已 in_review）
- 文档：`docs/` 及 Epic 122 下 #50（需求与方案，§8 切片 3）
