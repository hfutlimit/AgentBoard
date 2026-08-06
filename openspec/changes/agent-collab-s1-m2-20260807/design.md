# Design：S1 M2 MQ 事件总线泛化

> ID: agent-collab-s1-m2-20260807 · 上游：文档 #51 §6 / 文档 #50 §5

## 1. 目标

在 `agentboard/mq.py` 内**增量追加**通用工作流事件总线（WorkflowMessage + WorkflowTopology + PikaWorkflowBroker + InMemoryWorkflowBroker + WorkflowPublisher），Proposal 链路与既有 API 契约零改动。

## 2. 消息体 WorkflowMessage

```python
@dataclass(frozen=True)
class WorkflowMessage:
    event: str            # story.created / review.requested / ... 白名单校验
    entity_type: str      # story | task
    entity_id: int        # 定位信息（正数）
    ref_id: int | None    # comment_id / reviewer_id 等辅助定位，可空
    ts: str               # ISO UTC
```

- `from_bytes`/`from_dict` 严格校验：event 必须在 `WORKFLOW_EVENTS` 白名单；entity_type ∈ {story, task}；entity_id 正整数（显式拒绝 bool）；非法载荷抛 `MQMessageError` → 进死信。
- 铁律：消息只带定位信息，业务状态一律回查 DB（沿用 Epic 96 验证过的模式）。

## 3. 拓扑 WorkflowTopology

```
exchange agentboard.workflow        (topic, durable)
  ├─ queue <ns>.broadcast           绑定 workflow.broadcast.#     （认领型事件，竞争消费）
  └─ queue <ns>.agent.{agent_id}    绑定 workflow.agent.{agent_id}（定向通知，随注册幂等声明）
exchange agentboard.workflow.dlx    (direct, durable)
  └─ queue <ns>.dead
```

- 默认命名空间 `agentboard.workflow`；测试用 `AGENTBOARD_WORKFLOW_NAMESPACE=agentboard.workflow.test.<uuid>` 隔离。
- 事件 → 路由：
  - 广播：`story.created`（调度器竞争）、`story.ready`（developer 竞争）、`task.available`/`task.ready_for_review`（切片 2 预留）→ routing key `workflow.broadcast.{event}`
  - 定向：`review.requested`（→reviewer）、`review.rejected`（→作者）、`comment.replied`（→评审人/作者）→ routing key `workflow.agent.{agent_id}`
- 队列 durable；agent 队列随 `declare_agent_queue` 幂等声明，注销时保留（MVP 接受空队列，避免重建成本）。

## 4. Broker

### 4.1 PikaWorkflowBroker

独立实现（不继承 PikaBroker，避免 topic/direct 语义与消息类型耦合），复用 `MQConfig`（AGENTBOARD_MQ_URL）+ 既有异常类：

- `declare_topology()`：幂等声明 dlx exchange(direct) → dead 队列绑定；topic exchange → broadcast 队列绑定 `workflow.broadcast.#`
- `declare_agent_queue(agent_id)`：声明 `<ns>.agent.{agent_id}` + 绑定 `workflow.agent.{agent_id}`（已声明集合防重复）
- `publish(routing_key, message)`：confirm_delivery + mandatory；失败抛 MQError
- `consume(queue_name, handler, max_messages, idle_timeout, stop)`：basic_qos(prefetch=1) 竞争消费；ack / nack(requeue=False)→DLX
- `queue_depth(queue_name)`：被动声明查询；`purge`/`teardown` 供测试

### 4.2 InMemoryWorkflowBroker

- 数据结构：`bindings: dict[str, list[str]]`（queue_name → routing pattern 列表）、`_queues: dict[str, list[bytes]]`
- `publish(routing_key, message)`：遍历 bindings，`_topic_match(pattern, routing_key)` 命中则入队
- `consume(queue_name, handler, ...)`：从指定队列弹出；handler 返回 False 或抛异常 → 进 dead
- `_topic_match`：实现 RabbitMQ topic 匹配（`*` 单段、`#` 0+ 段、无通配符精确）
- `dead_letters()` / `queue_depth(queue, dead=False)` 供断言

## 5. 发布器 WorkflowPublisher

复制 ProposalPublisher 的成熟骨架：

- 加锁串行化发布（BlockingConnection 非线程安全，FastAPI 同步端点在线程池）
- 发布失败先 close 重建再试一次，仍失败记告警返回 False（best-effort，绝不抛给 REST）
- `enabled` 为 False（未配置 URL / 未注入 broker）→ 全部 no-op 返回 False，调用方回退轮询
- 进程级单例 `get_workflow_publisher()` + `set_workflow_publisher()`（测试注入）+ 一行式 `publish_workflow_event(...)`（任何异常都不上抛）

## 6. 事件清单（切片 1）

| 事件 | 路由 | ref_id 语义 | 消费方 |
|---|---|---|---|
| story.created | 广播 | — | 调度器 → 随机派 reviewer（M3） |
| review.requested | 定向 reviewer | reviewer_id | reviewer 拉取 story+评论 |
| review.rejected | 定向作者 | comment_id | 作者回复评论 |
| comment.replied | 定向 | comment_id | 评审人/作者复核 |
| story.ready | 广播 | — | developer 认领（切片 2） |
| task.available / task.ready_for_review / task.reviewed / task.rejected | 常量预留 | — | 切片 2 |

## 7. 兼容与回退

- `AGENTBOARD_MQ_URL` 为空 → WorkflowPublisher 全部 no-op，分配器与评审靠轮询（list_review_tasks）兜底，正确性不变；
- Proposal 命名空间 `agentboard.proposals` 与既有类零改动；
- 消费侧 worker 动作（M3 步骤 6）本次不实现，仅提供 consume 能力与单测验证。

## 8. 测试策略

- `tests/test_epic122_s1_m2_workflow_mq.py`：消息校验（合法/非法 event/entity_id/bool）、广播路由、定向路由（多 agent 隔离）、毒消息死信、topic 匹配（`#`/精确）、Publisher no-op 回退、注入 broker 发布消费闭环、与 Proposal 总线互不干扰；
- 回归：test_epic96_p2_rabbitmq_mq.py + test_epic122_agent_review.py 全绿。
