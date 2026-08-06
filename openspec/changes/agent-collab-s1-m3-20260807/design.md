# Design：S1 M3 事件源接入 + 分配器 Worker + MCP 评审工具

> ID: agent-collab-s1-m3-20260807 · 上游：文档 #50/#51/#52 + M1/M2 实现

## 1. 事件源接入（api.py）

四类端点发布事件（全部经 `publish_workflow_event`，**best-effort 不抛异常**）：

| 端点 | 事件 | 路由 | 载荷语义 |
|---|---|---|---|
| `POST /api/epics/{eid}/stories` | `story.created` | 广播 | entity_id=story.id，ref_id=epic.id |
| `POST /api/stories/{sid}/assign-reviewer` | `review.requested` | 定向（reviewer Agent）| entity_id=story.id，ref_id=reviewer user_id；reviewer 未绑定 Agent → 退化为广播 |
| `POST /api/stories/{sid}/review` | approve → `story.ready`；reject → `review.rejected` | 广播 | ready: ref_id=reviewer user_id；rejected: ref_id=review_round |
| `POST /api/stories/{sid}/comments` | `comment.replied` | 定向（reviewer Agent）| entity_id=story.id，ref_id=comment.id；无绑定则广播 |

定向投递的 user_id → agent_id 解析：`SELECT agent FROM agents WHERE user_id = reviewer_id`
（api.py 内联查询，agent 未注册时退化为广播——事件仍可达，仅少定向性）。

**约定**：`review.rejected` / `comment.replied` 的**业务收敛**由 Reviewer / 作者 Agent
各自订阅定向队列完成（Agent 侧职责），分配器 Worker 只记录日志不介入决策。

## 2. 分配器 Worker（workflow_worker.py 新建）

```
WorkflowConsumerConfig.from_env()  →  api_url / token / poll_interval / batch_size / mq / namespace
WorkflowConsumer
 ├─ handle_message(msg)            事件分发
 │    story.created      → _assign_reviewer(entity_id)
 │    story.ready/task.* → 日志（切片 2 预留）
 │    review.rejected/comment.replied → 日志（Agent 定向订阅处理）
 ├─ _assign_reviewer(sid)          POST /api/stories/{sid}/assign-reviewer
 │    200/201 → 已指派；404 → 忽略；422（无在线 reviewer）→ warn + ack（轮询兜底）；网络异常 → False（重投）
 ├─ run_mq_forever()               PikaWorkflowBroker 消费 broadcast 队列；无 MQ 回退 run_forever
 ├─ run_poll_once()                轮询 GET /api/stories?status=backlog → 未指派者触发分配（幂等）
 └─ run_forever()                  常驻轮询（interval 可配）
CLI: python -m agentboard.workflow_worker --mq | --loop | --once
```

- 幂等性：`assign-reviewer` 服务端 CAS（`status=backlog AND reviewer_id IS NULL`），
  重复触发天然安全；
- 最终一致：无在线 reviewer 时事件 ack 丢弃 + 轮询兜底扫描 backlog，
  消息丢失不产生漏单。

## 3. MCP 评审工具（mcp_server.py 新增 6 个）

| 工具 | REST | 说明 |
|---|---|---|
| `agent_register(agent_id, name, roles, capabilities, cli_command, auth_key)` | `POST /api/agents/register` | 幂等注册，绑定当前 MCP 身份 |
| `agent_heartbeat(agent_id)` | `POST /api/agents/{id}/heartbeat` | 置在线 |
| `agent_deregister(agent_id)` | `POST /api/agents/{id}/deregister` | 下线（保留记录）|
| `list_agents(online, role)` | `GET /api/agents` | 过滤列表 |
| `review_story(story_id, verdict, comment)` | `POST /api/stories/{sid}/review` | approve/reject + 评论 |
| `list_review_tasks(status)` | `GET /api/stories?reviewer_id=me` | 我的评审任务 |

全部走 `_http`，与既有工具风格一致；Epic 97 AST 护栏（未定义调用 / /api 前缀）自动覆盖。

## 4. 兼容与回退

- Story 创建仍默认 backlog，评审流由事件驱动显式开启；Epic 96 proposal 转化链路零影响；
- MQ 未配置：分配器回退轮询，正确性不变；事件发布 no-op，REST 零影响；
- 零新增第三方依赖；双后端兼容；18001 不触碰（MCP 工具验证靠自包含测试 + 独立运维窗口）。
