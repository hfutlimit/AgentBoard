# Agent 间自动协作闭环 —— 需求总结

> 状态：需求梳理稿（尚未进入 OpenSpec Change）
> 日期：2026-08-06
> 目标：让多个 Agent 通过 AgentBoard（MCP + RabbitMQ + CLI）自动完成
> 「需求澄清 → Story 评审 → 开发认领 → 代码评审」的全流程闭环，人类只在必要时介入。

---

## 1. 背景与目标

当前 AgentBoard 已具备：Proposal 澄清回路（Epic 96，含 MQ 消息总线）、三实体评论、
Task/Story/Epic 状态机、Webhook、Schedule/Run、111 个 MCP 工具。**但协作仍以
「单 Agent 对系统」为主**——一个 Agent 创建 Story 后，没有其他 Agent 参与评审，
也没有事件驱动的接力通知。

本需求把 AgentBoard 升级为 **「多 Agent 工作流编排中枢」**：

1. **Agent 自注册**：Agent 主动通过 MCP 注册自己的身份、角色与能力；
2. **Story 评审闭环**：Story 创建后随机分配其他 Agent 评审，评论往返直至达成一致，通过后进入 ready 态；
3. **开发任务分配**：Story ready 后广播给可用 Agent 竞争认领 Task；
4. **Task 评审闭环**：开发完成后由其他 Agent 评审，通过才置 done；
5. **全程事件驱动**：状态流转与评论回复均通过 RabbitMQ 通知相关 Agent，Agent 经 MCP/CLI 回查数据库执行动作。

---

## 2. 角色模型（Agent Registry）

Agent 通过 MCP 主动注册身份，系统维护一张注册表：

| 字段 | 说明 |
|---|---|
| `agent_id` | 注册时生成或由 Agent 指定，全局唯一 |
| `name` | 显示名（如 `dev-frontend` / `reviewer-1`） |
| `role` | `requester`（需求提出）/ `reviewer`（评审）/ `developer`（开发）——可多选 |
| `capabilities` | 能力标签（前端/后端/测试/文档…），用于开发任务匹配 |
| `cli_command` | 该 Agent 的 CLI 拉起命令模板（复用 Worker 的 `SubprocessAgentInvoker`） |
| `online` / `last_heartbeat` | 在线状态，由 `heartbeat` 维护 |
| `auth_key` | 绑定的 `abk_` API Key，所有动作以此身份归属 |

配套能力：
- **Agent 身份与用户解耦**：现有 `auth_register` 是「人」注册；Agent 注册是独立实体，可挂项目成员权限；
- **随机/公平分配**：评审人从「在线且非作者本人」的 reviewer 集合中随机选取；开发人从「在线且能力匹配」的 developer 集合中竞争认领；
- **心跳与超时剔除**：`heartbeat` 已存在，复用即可，过期 agent 不参与分配。

---

## 3. 端到端流程

```
[A] 需求澄清（已实现，Epic 96）
    Agent A: spec_proposal → proposal_convert → Story(backlog)
                    │
[B] Story 评审闭环（新增）                                        RabbitMQ 事件
    Story 创建 ──────────────────────────────────────────► story.created
        │ 随机挑选 reviewer（在线 ∩ reviewer 角色 ∩ ≠A）
        ▼
    reviewer 拉取 story + comments（MCP）                       review.requested
        ├─ approve ──► Story → ready ──────────────────► story.ready
        │                                                        │
        └─ reject ──► 留 comment ──────────────────────► review.rejected
                          │
        A 收到通知 → 回复/改描述 ──────────────────────► comment.replied
                          │ （重复直到 approve，设轮次上限护栏）
                          ▼
[C] 开发任务分配（新增）
    Story ready ────────────────────────────────────────► task.available(广播)
        │ 在线 developer 竞争认领（CAS，复用 claim 模式）
        ▼
    dev 领取 Task（assignee 回填）→ 开发 → 完成
        │
[D] Task 评审闭环（新增）                                        Task(dev) 完成 ──► task.ready_for_review
        │ 随机挑选其他 agent 评审（≠开发者）
        ▼
    reviewer 拉取 task + 评论
        ├─ approve ──► Task → done（终态）─────────────► task.reviewed
        └─ reject ──► 留 comment，Task → in_progress ──► review.rejected
                          │ dev 收到通知回复，循环（轮次上限护栏）
                          ▼
                    直到 approve
```

**人类角色**：默认全自动；可配置「Proposal 终审」「Story 终审」等人工门禁位（复用
现有 `converged → 人工终审 → story_created` 模式，可关）。

---

## 4. 状态机扩展

### 4.1 Story 状态机（需扩展）

现状：`backlog → todo → in_progress → in_review → verifying → done`

新增「需求明确性评审」阶段，建议扩展为：

```
backlog ──创建──► pending_review ──reviewer approve──► ready ──开发分配──► in_progress
    │                   │                                  │
    │                   └─reject（留 comment）─────────────┘
    │                     pending_review 与 ready 间可往返（评论收敛）
    ▼
in_progress → in_review → verifying → done
```

- `pending_review`：Story 已创建、待评审（可设 `reviewer_id` 回填被指派人）；
- `ready`：评审通过、需求明确、可进入开发——**「ready」为新增状态**（当前枚举无）；
- 评论收敛期 Story 可停留在 `pending_review`，状态不变，靠评论+MCP 通知推进；
- 约束：`pending_review → ready` 仅允许被选中的 reviewer 操作（权限校验），避免随意置 ready。

### 4.2 Task 状态机（复用现有，补充事件）

现状已支持评审：
`IN_PROGRESS → IN_REVIEW → DONE / IN_PROGRESS`（驳回）。

新增的是**自动事件**而非状态：
- dev 完成开发 → 置 `IN_REVIEW` 并触发 `task.ready_for_review` 通知；
- reviewer 驳回 → 置 `IN_PROGRESS` + 评论 + `review.rejected` 通知 dev；
- reviewer 通过 → 置 `DONE`（终态）。

---

## 5. MCP 工具清单（新增 7 个左右）

| 工具 | 说明 | 归属角色 |
|---|---|---|
| `agent_register` | 注册身份（agent_id/name/role/capabilities/cli/auth_key） | 所有 |
| `agent_deregister` / `agent_heartbeat` | 注销 / 心跳（heartbeat 已有则复用） | 所有 |
| `review_story` | 对 Story 投 approve/reject 票 + 评论（CAS：仅被指派 reviewer 有效） | reviewer |
| `list_review_tasks` | 拉取分配给我 / 待认领的评审任务（story+task 统一） | reviewer |
| `claim_development_task` | 竞争认领可用 Task（CAS 原子，参照 proposal claim） | developer |
| `submit_task_for_review` | dev 开发完成 → IN_REVIEW + 触发通知 | developer |
| `review_task` | 对 Task 投 approve/reject 票 + 评论 | reviewer |
| `list_agents` | 查看在线 Agent 与能力（运营/调试） | 任意 |

复用既有：`create_story / update_story / list_comments / add_story_comment /
add_comment / set_status / claim_task / spec_proposal / proposal_convert / get_project_memory / append_agent_memory`。

---

## 6. RabbitMQ 事件总线（泛化现 mq.py）

现状 `agentboard/mq.py` 是 **Proposal 专用**（`ProposalMessage`、命名空间
`agentboard.proposals`）。本需求要求泛化为通用事件总线，新增事件类型（同实体可拆
独立队列，或单队列 + `type` 字段二选一，见 §8 待决策）：

| 事件 | 载荷（仅定位信息） | 消费方 |
|---|---|---|
| `story.created` | `{story_id}` | 调度器 → 随机派 reviewer |
| `review.requested` | `{story_id, reviewer_id}` | 目标 reviewer（定向投递） |
| `review.rejected` | `{story_id, comment_id, by}` | Story 作者（定向） |
| `comment.replied` | `{entity_type, entity_id, comment_id, by}` | 评审人 / 作者（定向） |
| `story.ready` | `{story_id}` | 广播 → 可用 developer |
| `task.available` | `{story_id, task_id}` | 广播 → developer 竞争认领 |
| `task.ready_for_review` | `{task_id}` | 调度器 → 随机派 reviewer |
| `task.reviewed` / `task.rejected` | `{task_id, by}` | dev / 作者（定向） |

**必须沿用现有 MQ 设计铁律**：
1. **DB 是唯一事实源**，消息只带定位信息（id），消费者一律回查 REST 再决策；
2. at-least-once + 服务端 CAS 认领，重投不产生重复轮次；
3. DLX 死信队列 + 维护线程自愈重投（`sweep`），消息丢了由轮询兜底；
4. MQ 未配置（`AGENTBOARD_MQ_URL` 为空）时**整体回退轮询**，正确性不受影响。

**定向投递与广播**：定向（reviewer/作者）需要**每 Agent 一个队列**（queue-per-agent，
routing key = `agent.{agent_id}`）；广播（可用 developer）用 fanout 或全量 routing。
两种模式当前 topology 均不支持，需扩展（见 §7）。

---

## 7. 与现有能力的映射（已有 vs 新增）

| 能力 | 现状 | 本需求动作 |
|---|---|---|
| Proposal 澄清回路 | ✅ 完整（REST+MQ+Worker+MCP 7 工具） | 直接复用，作为入口 |
| Proposal → Story 转化 | ✅ `proposal_convert` 幂等 | 复用 |
| 三实体评论 | ✅ comments 表 + MCP 工具 | 复用，作为评审意见载体 |
| Task 状态机（含 in_review 往返） | ✅ | 复用，补事件触发 |
| MCP 工具基础设施（111 个） | ✅ | 追加 7-8 个新工具 |
| Webhook 签名派发 | ✅ `service.fire_webhook` | 建议把业务事件接入，作为 MQ 之外的推送通道 |
| Agent 身份注册 | ❌ 无 | **新增** agents 表 + 注册 MCP 工具 |
| Story `ready` / `pending_review` 状态 | ❌ 无 | **新增**状态 + 迁移 + 状态机扩展 |
| Story 随机评审分配 | ❌ 无 | **新增**分配器（DB 查询 + CAS） |
| Task 开发竞争认领 | ⚠️ `claim_task` 已有，无分配广播 | 补 `task.available` 广播 + 认领契约 |
| 通用 MQ 事件总线 | ⚠️ 仅 Proposal 专用 | **泛化**为多事件类型 + 定向/广播拓扑 |
| 多角色 Worker | ⚠️ 单一澄清 Worker | 扩展为 reviewer/developer 双角色（或注册驱动） |

---

## 8. 待决策问题

1. **MQ 拓扑方案**：单队列 + `type` 字段（简单，但定向投递要消费后过滤） vs
   多队列（`agent.{id}` 定向 + `broadcast` 广播，天然路由，但队列数随 Agent 增长）；
2. **Story ready 的语义**：新增 `ready` 枚举 vs 复用现有 `todo`（不推荐，语义漂移）；
3. **评审分配公平性**：纯随机 vs 轮询（round-robin）vs 最少负载（按当前被评审数）；
4. **评审轮次护栏**：默认上限（如 5 轮）后自动置 `failed`/`blocked` 转人工，可配置；
5. **人工门禁位**：哪些环节默认全自动、哪些需要人确认（Proposal 终审已有人工位）；
6. **Agent 与项目权限**：Agent 注册是否绑定项目成员（受 `project_access_middleware` 约束），还是全局服务账号（`is_admin` 绕过）；
7. **评审强度**：需要 N 个 reviewer 都 approve（多数决）还是 1 个即可（MVP 建议 1 个）；
8. **RabbitMQ 命名空间**：沿用 `agentboard.proposals` 还是新建 `agentboard.workflow`（建议新建，避免与现有 Worker 混用）。

---

## 9. 落地建议（切片）

- **切片 1（MVP 闭环）**：Agent 注册表 + Story `pending_review/ready` 状态 + `review_story` + MQ 事件泛化 + 定向通知；
- **切片 2**：Task 开发认领广播 + `submit_task_for_review` / `review_task`；
- **切片 3**：Webhook 事件接入、评审统计与运营视图、护栏调优（轮次/超时/多数决）；
- 每个切片遵循 OpenSpec：`proposal.md → design.md → tasks.md`，含 Alembic 迁移与自动化测试（沿用 `InMemoryBroker` 与 CAS 单测模式）。
