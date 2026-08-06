# Tasks：S1 M2 实现清单

> ID: agent-collab-s1-m2-20260807 · 全部 `[x]` 表示已交付

## 实施步骤

- [x] **事件常量与消息**：`agentboard/mq.py` — `WORKFLOW_EVENTS` 白名单（story.created/review.requested/review.rejected/comment.replied/story.ready + task.* 预留）、`WorkflowMessage`（event/entity_type/entity_id/ref_id/ts，from_bytes 严格校验）
- [x] **拓扑**：`WorkflowTopology` — 命名空间 agentboard.workflow，topic 交换机 + `<ns>.broadcast`（绑定 workflow.broadcast.#）+ `<ns>.agent.{agent_id}`（绑定 workflow.agent.{agent_id}）+ DLX dead
- [x] **Broker**：`PikaWorkflowBroker`（topic publish confirm + consume prefetch/DLX + declare_agent_queue 幂等）+ `InMemoryWorkflowBroker`（_topic_match 匹配 + 多队列 + dead）
- [x] **发布器**：`WorkflowPublisher`（best-effort + 加锁串行化 + 断线自愈 + no-op 回退）+ 单例 `get_workflow_publisher`/`set_workflow_publisher` + 一行式 `publish_workflow_event`
- [x] **测试**：`tests/test_epic122_s1_m2_workflow_mq.py`（消息校验/广播定向路由/毒消息死信/topic 匹配/回退 no-op/发布消费闭环/Proposal 隔离）
- [x] **回归**：test_epic96_p2_rabbitmq_mq + test_epic122_agent_review 全绿

## 验证记录

- pytest 27 passed（tests/test_epic122_s1_m2_workflow_mq.py：消息校验/topic 匹配/广播定向路由/毒消息死信/回退 no-op/发布消费闭环/Proposal 隔离）
- 回归 21 passed / 15 skipped（test_epic96_p2_rabbitmq_mq + test_epic122_agent_review + test_crud_smoke，RabbitMQ 集成用例无 broker 跳过属预期）
- docker restart agentboard-api-1（未触碰 18001），API health 200
- Playwright E2E：登录 / 项目页 / 看板视图（board 开关）渲染正常，0 console/page 错误

## 状态

Task 1008 → in_review；Story 230 / Epic 122 持续推进（下一步 S1 M3：MCP 工具 + 分配器 worker）。
