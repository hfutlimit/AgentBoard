# Change Proposal：多 Agent 协作闭环 S3 M1（Webhook 事件接入）

> ID: agent-collab-s3-m1-20260807 · Epic 122 / Story 232 / Task 1013

## 问题

Epic 122 已交付 S1（Agent 注册 + Story 评审闭环）、S2（开发任务认领 + Task 评审闭环），
事件驱动依赖 RabbitMQ 事件总线（`agentboard.workflow` 命名空间）。但 Webhook 基建
（`WebhookConfig` + `fire_webhook`）自建成就**没有任何业务事件调用**——外部系统 /
常驻 Runner 无法经 HTTP 订阅多 Agent 协作的关键节点，协作状态变化只能靠轮询感知，
与「事件驱动」目标相悖。文档 #50 §8 切片 3 明确列出「Webhook 事件接入」为待交付项。

## 目标

1. **Webhook 事件派发**：`service.fire_webhooks_for_event` —— 按项目查 enabled
   WebhookConfig，events 配置过滤（空列表 = 订阅全部；非空精确匹配），逐个派发、
   单 webhook 异常隔离，返回 `{matched, succeeded}` 统计；
2. **业务事件接入**：`api._notify_webhooks`（best-effort）接入全部既有 workflow
   事件点 —— `story.created` / `review.requested` / `story.ready` / `review.rejected` /
   `comment.replied` / `task.ready_for_review` / `task.reviewed` / `task.rejected`；
3. **事件语义统一**：Webhook 事件名直接复用 `mq.EVENT_*` 常量，与 MQ 事件同构，
   外部消费者用同一套事件语义订阅任一通道；
4. **铁律一致**：payload 只带定位信息（实体 id / status / ref），状态一律回查 DB；
   Webhook 派发失败（网络/DB/异常）绝不阻断主业务。

## 非目标

评审统计报表与运营视图、护栏调优（轮次/超时/多数决）、Webhook 重试队列与死信
（MVP 同步单发 + 调用方轮询兜底）—— 属切片 3 M2 或后续迭代。

## 关键设计

- **双通道平行**：RabbitMQ（Agent 间事件总线，定向+广播）与 Webhook（面向外部
  系统的 HTTP 通道）共用同一事件名与定位信息语义，互不替代、互不阻塞；
- **best-effort 双保险**：`fire_webhooks_for_event` 内单 webhook try/except 隔离 +
  `_notify_webhooks` 整函数 try/except 兜底，Webhook 任何故障不影响业务成功返回；
- **复用既有签名基建**：`fire_webhook` 已实现 HMAC-SHA256 签名
  （`X-AgentBoard-Signature` / `X-AgentBoard-Timestamp`），零改动复用；
- **Story 无 project_id 列**：事件点经 `st.epic_id → epic.project_id` 解析（Story
  模型无直接 project 外键，避免造表迁移）。

## 验收

1. pytest `test_epic122_s3m1.py` 17 用例：过滤语义（无/disabled/空=全部/精确/不匹配/
   跨项目隔离）、异常隔离（返回 False 与抛异常两种）、统计正确；API 接入点断言
   （create_story / review_story approve+reject / submit-review / review_task
   approve+reject 各自事件名与 project_id）；`_notify_webhooks` best-effort
   （fire_webhooks_for_event 抛异常主业务仍 201）；Epic 97 AST 护栏零 `_api(` 残留 +
   webhook 工具注册；
2. 既有测试零回归（epic122 s1/s2 + agent_review + crud_smoke）；
3. 零新增第三方依赖；不触碰 18001。
