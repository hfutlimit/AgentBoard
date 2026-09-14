# Design — Agent Handoff & Project Memory

## 1. Entity 边界（先定义对象，再谈功能）

外部 review 第 5 轮要求冻结五个对象。逐个对齐现状：

| Entity | 回答 | 现状 | 结论 |
|---|---|---|---|
| **Agent** | 谁 | `Agent`（`agent_id` / `name` / `capabilities` / `model`）—— 身份，跨项目 | 已存在，边界清晰 |
| **Worker** | 在哪跑 | `Worker`（`worker_id` / `hostname` / `status`）+ `AgentInstance`（一 worker 多 agent） | 已存在；**worker 健康与 agent 健康已是两套**（`expire_stale_worker_heartbeats` vs `expire_stale_agent_heartbeats`），不要合并 |
| **AgentRun** | 一次执行 | `agent_runs`（`status` / `last_progress_*` / `lease_expires_at` / `agent_registry_id`） | 已存在 |
| **Attempt** | 第几次试 | ⚠ **三种 attempt 并存**：`agent_runs`（执行）/ `task_assignments`（派发租约）/ `message_attempts`（MQ 投递） | **本变更只做命名与职责收敛，不新增表** |
| **Memory** | 留下什么 | `Document.type='memory'` 两层 title 前缀隔离 | 已存在，缺结构 |

**Attempt 收敛建议**（设计层，不落库）：对外口径统一为「attempt = 一次 AgentRun」，`TaskAssignment` 是**调度关系不是执行**，`MessageAttempt` 是**投递重试不是执行**。三者在文档与 UI 里不要都叫 attempt。

## 2. 两条流必须分开

| | Workflow Event Stream | Agent Knowledge Stream |
|---|---|---|
| 承载 | `workflow_run_events`（已存在） | 新建（本变更） |
| 关注 | **状态变化** | **经验** |
| 事件 | `workflow_started` / `task_assigned` / `review_completed` … | `analysis` / `decision` / `discovery` / `failure` / `handoff` |
| 性质 | **封闭枚举**，19 类 + 必填 payload 校验 | **开放集合** |
| 量级 | 一次 run 十几条 | 一次执行几十到几百条 |
| 消费 | 状态机 / timeline | 记忆沉淀 / 交接 / UI 过程展示 |

**为什么不合并**：`workflow_run_events` 背后是 `EVENT_TYPE_REQUIRED_KEYS` 的校验枚举。知识事件是开放集合，放进去只能放弃校验或把枚举改成开放 —— 等于退掉 slice 2 最值钱的部分。另外 `workflow_run_events.workflow_run_id` 是 **NOT NULL FK**（`models.py:107-108`），run-less 的知识条目根本挂不进去。

仓库已有同类先例：契约里的 `RETRY_KINDS_UI_VISIBLE`（`mq_delivery` 不进 normal UI）——「可见性」这个轴已有表达方式。

## 3. Handoff Package 契约

生成时机：`soft_takeover_run`（agent 被判 dead）与任务被人工退回时 **各生成一份**，归属到 task。

```json
{
  "task_id": 123,
  "from_run_id": 456,
  "to_run_id": null,
  "status": "partial",
  "completed":      ["DB migration", "API endpoint"],
  "remaining":      ["Frontend integration", "Unit tests"],
  "known_issues":   ["Image size validation pending"],
  "changed_files":  ["UserController.cs"],
  "next_steps":     ["Add frontend component"],
  "verification":   ["pytest tests/test_avatar.py 未通过：断言 3"],
  "created_at": "..."
}
```

**加载契约**：新 agent 启动时**必须**读到上一 attempt 的 handoff（若存在）+ `get_project_memory` + `git diff` + 测试结果。

**边界**：handoff 只传递上下文，**不做代码级差异接管**（不自动 rebase A 的未提交改动）。

## 4. Project Memory：六面结构 + 防腐

现状（`mcp_server.py:1350`）：`merged = (old + "\n\n" + content)` —— 无限追加，正是"变成垃圾文档"的路径。

目标结构（沿用 `Document.type='memory'`，**不新建表**；分面用 title 前缀或 content 内小节，二选一在任务阶段定）：

```
项目记忆
├── Decisions          已定的技术决策 + 理由 + 被否决的方案
├── Architecture       架构约束 / 不变量
├── Known Issues       已知问题与坑
├── Failed Attempts    试过且失败的路（**最容易被忽略、价值最高**）
└── Coding Rules       编码规范 / 约定
```

**防腐策略（本变更的核心，直接决定长期价值）：**

| 问题 | 策略 |
|---|---|
| 重复追加同一结论 | 写入前做**规范化去重**（同义同义改写要判为同一条） |
| 失效结论越积越多 | 每条记忆带**时效与适用范围**；被新决策取代的标 `superseded_by` 而非删除 |
| 自由文本无权威等级 | 引入**来源等级**（人写的 > agent 推断的 > 单次观察），冲突时高等级覆盖 |
| 无限增长 | **保留策略**：Failed Attempts 与 Decisions 长期保留，一次性观察类定期归档 |

## 5. 并行策略：按隔离，不按 agent

外部 review 建议「第一阶段禁止一个 AgentRun 并行多个 task，因为上下文污染」。现状比这个建议**更精确**：

```
processors/workspace/models.py:19-21
  existing_checkout 复用用户既有工作目录，并行任务必然互相污染，
  因此 max_parallel 被强制收敛为 1（FORCED_MAX_PARALLEL）
```

即不变式是：**共享 checkout ⇒ 串行；`worktree` / `fresh_clone` ⇒ 隔离，可并行**。

- `uq_task_assignment_active_slot` = `(task_id, active_slot)` → 一 task 至多一个活跃分配，但**不限制一个 agent 同时接多 task**。
- 因此「一个 agent 并行多 task」当前**未被禁止但也未被鼓励**；真正的护栏在工作区层。

**结论**：不引入「按 agent 串行」的新约束（会误禁已隔离的安全并行）；改为**把工作区层的 `max_parallel` 显式化到 agent-run 受理口径**：共享 checkout 时拒绝并发受理并给出结构化原因。

## 6. 路线图（外部 review 的 Phase 划分，收编进本变更）

| Phase | 内容 | 归属 |
|---|---|---|
| 1 | Proposal → Task → Agent Execution → Progress → Completion | 已落地（slice 1+2 + progress） |
| 2 | Failure Recovery → Agent Replacement → **Handoff** | **本变更**（Rel. 侧归 Epic 168） |
| 3 | Multi-agent Collaboration / Review / Debate | 后续 |
| 4 | Project Intelligence（长期记忆） | 本变更的 Memory 防腐是其前置 |

## 7. 非目标

- 不做完整 event sourcing 回放。
- 不做代码级接管（自动合并 A 的未提交改动）。
- 不把聊天记录当记忆存（原始对话轮次不是知识）。
- 不在本变更内改 `workflow_run_events` 的 19 类契约。
