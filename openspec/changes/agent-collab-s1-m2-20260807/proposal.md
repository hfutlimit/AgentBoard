# Change Proposal：多 Agent 协作闭环 S1 M2（MQ 事件总线泛化）

> ID: agent-collab-s1-m2-20260807 · Epic 122 / Story 230 / Task 1008

## 问题

Epic 122 多 Agent 协作闭环需要「事件驱动接力」：Story 评审指派、评审驳回、评论回复、评审通过等都需要通知对应 Agent。现状 `agentboard/mq.py` 是 Proposal 澄清回路专用总线（`ProposalMessage` + direct 交换机 + 单一 work 队列），无法表达「广播给全体开发者 / 定向通知某 Agent」两类拓扑，也没有通用事件消息体。

## 目标

将 mq.py 泛化为**通用工作流事件总线**（增量追加，Proposal 链路零改动）：

1. **WorkflowMessage**：通用事件消息（event / entity_type / entity_id / ref_id / ts），事件白名单校验（毒消息防护），铁律不变——消息只带定位信息，状态一律回查 DB；
2. **WorkflowTopology**：命名空间 `agentboard.workflow`，**topic 交换机** + 广播队列（绑定 `workflow.broadcast.#`，竞争消费，story.created/story.ready/task.*）+ 每 Agent 定向队列（`agent.{agent_id}`，绑定 `workflow.agent.{agent_id}`，review.requested/review.rejected/comment.replied）+ DLX 死信；
3. **PikaWorkflowBroker**：topic 语义 publish（confirm_delivery + mandatory）/ consume（prefetch + DLX nack）/ declare_agent_queue（幂等）；
4. **InMemoryWorkflowBroker**：内存 broker，routing key 匹配 + 多队列，供单测与离线降级；
5. **WorkflowPublisher**：best-effort（MQ 故障不影响 REST）+ 加锁串行化 + 断线自愈 + 未配置回退轮询 no-op；事件常量 + 一行式发布入口。

## 非目标（后续切片）

- MCP 工具（agent_register/review_story/list_review_tasks）—— S1 M3；
- story.created → 随机指派 reviewer 的消费者动作（分配器 worker）—— S1 M3；
- Task 开发认领广播（task.available 消费侧）—— 切片 2。

## 关键设计

- **与 Proposal 总线完全隔离**：新命名空间 `agentboard.workflow`（默认，可用 `AGENTBOARD_WORKFLOW_NAMESPACE` 覆盖供测试隔离）；`ProposalMessage`/`ProposalPublisher`/`Topology` 一字不改。
- **topic 语义**：广播绑定 `workflow.broadcast.#`（`#` 匹配 0+ 段，`workflow.broadcast.story.created` 命中）；定向绑定精确 `workflow.agent.{agent_id}`；事件类型放消息体而非 routing key，路由只负责投递。
- **发布 API 语义**：`publish(event, entity_type, entity_id, ref_id=None, agent_id=None)` —— `agent_id` 非空走定向，空走广播；`ref_id` 记录 comment_id / reviewer_id 等定位信息。
- **复用既有成熟模式**：confirm_delivery 防静默丢、DLX 死信 + requeue=False、发布加锁串行化（BlockingConnection 非线程安全）、失败重连重试一次、未启用整体 no-op 回退轮询。

## 验收

1. 单测（InMemoryWorkflowBroker）：广播事件路由到 broadcast 队列、定向事件只路由到目标 agent 队列、毒消息（非法 event/entity_id）进死信、WorkflowMessage 校验拒绝非法载荷；
2. WorkflowPublisher 未配置 URL 时 publish 返回 False 且不抛异常（回退 no-op）；注入 broker 时事件可被消费；
3. 与 Proposal 总线隔离：既有 test_epic96_p2_rabbitmq_mq.py 零改动零回归；pytest 新用例全绿；
4. 零新增第三方依赖（pika 已存在）；不触碰 18001。
