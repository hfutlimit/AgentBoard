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

## 2. 三条流，不是两条

| 流 | 承载 | 关注 | 性质 | 保留 | 禁止混入 |
|---|---|---|---|---|---|
| **Workflow events** | `workflow_run_events`（已有，story 446） | 状态变化 | **封闭枚举** 19 类 + 必填 payload 校验 | 长期 | tool_call / 经验 |
| **Activity** | 新建 `agent_run_activities`（story 451） | 过程（读了哪个文件、跑了哪条命令） | **开放集合**，高量 | **短周期** | `workflow_run_events` |
| **Knowledge** | **落到既有 `learnings` 表**（story 458） | 可晋升的经验 | 结构化 + 溯源 + confidence | 晋升后长期 | activity 原文 |

**为什么 knowledge 没有第三次「新建」**：`learnings` 已经是带溯源与置信度的知识条目（见 §2.5），再建 `agent_knowledge_entries` 就是第三套记忆。

**「test failed」归哪条**：执行期进 **activity**（过程事实）；只有当它**总结成一条可复用教训**（如「用 ORM Include 做嵌套查询 → N+1，已弃」）时才**晋升**为 knowledge 条目。

**晋升链（本设计的核心机制）**：

```
activity 原文 / 一次分析
        ↓ 晋升（agent 显式 or 评审后确认）
learnings 条目（机器检索索引，带 provenance + confidence）
        ↓ 晋升（人可读、可被规范引用）
Project Memory 分面（人读真源，可 supersede）
```

Handoff Package 是**任务级实体**，不是第四条流，也不是第六个记忆面。

**为什么 workflow events 不吸收后两者**：`workflow_run_events` 背后是 `EVENT_TYPE_REQUIRED_KEYS` 的校验枚举，知识/活动是开放集合，放进去只能放弃校验或把枚举改成开放 —— 等于退掉 slice 2 最值钱的部分。另外 `workflow_run_events.workflow_run_id` 是 **NOT NULL FK**（`models.py:107-108`），run-less 条目根本挂不进去。仓库已有同类先例：`RETRY_KINDS_UI_VISIBLE`（`mq_delivery` 不进 normal UI）。

## 2.5 Memory / learnings / Knowledge 三层边界（**已冻结，实现不得新增第三套**）

Epic 155 的纠错学习库已经落地并**接进了 prompt**，本变更必须与它划界而不是并行：

| 层 | 载体 | 用途 | 证据 |
|---|---|---|---|
| **机器检索索引** | `learnings` 表 | 按 project / agent / work_type 检索后**注入 agent prompt** | `features/learning/models.py:158-191`；写入 `scheduling/behavior_router.py:322`；检索 `processors/learning/retriever.py:105 learning_retriever`；注入 `processors/behavior/context_builder.py:508 _resolve_learnings` → 渲染成 `- [{category}] {summary}`（:208） |
| **人读真源** | `Document.type='memory'` 五面 | 人可读、可评审、可 supersede 的规范 | `mcp_server.py:1313/1337` |
| **晋升通道** | 无独立表 | activity / 分析 → learnings → Memory 分面 | 本文 §2 |

`learnings` 已有列与两面记忆的对应关系：

| `learnings` 列 | 对应 |
|---|---|
| `category`（5 类：`accepted_review_feedback` / `review_judgment_reversal` / `qa_defect` / `execution_failure` / `project_convention`） | ≈ knowledge 类型 |
| `summary` / `lesson` | ≈ 条目摘要 / 正文 |
| `source_run_id` / `source_task_id` / `source_review_id` | **已满足「可追溯到 attempt」** |
| `confidence` | ≈ 权威等级 |
| `agent_id` / `work_type` / `tags_json` | 检索维度 |

**冻结决定**：

1. **不新建 `agent_knowledge_entries` 表**。Story 458 改为「定义 learnings 的写入面 + 晋升到 Memory 分面的通道」。
2. `project_convention` category 与 Memory 的 **Coding Rules** 面对应；`execution_failure` 与 **Failed Attempts** 面对应 —— 晋升时按此映射，不再造第二套分类。
3. 防腐（story 456）**同时适用于 `learnings` 与 Memory 分面**：`learnings` 目前无 supersede、无保留策略，是同一类腐化风险。
4. 两面**不做双向同步**：Memory 是人读快照，`learnings` 是机器索引；不一致时以 Memory 分面为人读准，以 `learnings` 为检索准，并在升迁时显式记 `source_run_id`。

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

**加载契约（区分强制与可选，每项都要有来源）**：

| 项 | 强度 | 来源 |
|---|---|---|
| 上一 attempt 的 Handoff Package（若存在） | **强制** | story 457 落库的 handoff 实体，按 `task_id` 取最新一条 |
| `get_project_memory` | **强制** | 既有 MCP 工具（项目级 + 该 agent 专有），会话启动即调 |
| `git diff` | **可选** | 相对**该 task 认领时的基线 commit**（story 459 受理时记录），由 worker 侧在 workspace 内执行；没有基线就不给 diff，不得猜 |
| 测试结果 | **可选** | 优先取 handoff 的 `verification` 字段；其次取 activity 中 `kind=command` 且判定为测试的最后一条；取不到就显式标「无测试证据」 |

⚠ 不要把「可选」写成「强制」——没有基线 commit 时 `git diff` 无法定义，强制加载会变成凭猜测造内容。

**边界**：handoff 只传递上下文，**不做代码级差异接管**（不自动 rebase A 的未提交改动）。

## 4. Project Memory：五面结构 + 防腐

现状（`mcp_server.py:1350`）：`merged = (old + "\n\n" + content)` —— 无限追加，正是"变成垃圾文档"的路径。

目标结构（**五面**，不是六面；Handoff 是任务级独立产物，见 §3）：

```
项目记忆
├── Decisions          已定的技术决策 + 理由 + 被否决的方案
├── Architecture       架构约束 / 不变量
├── Known Issues       已知问题与坑
├── Failed Attempts    试过且失败的路（**最容易被忽略、价值最高**）
└── Coding Rules       编码规范 / 约定
```

### 4.1 存储方案（**已冻结，实现照做，不再"任务阶段定"**）

| 决定 | 取值 | 理由 |
|---|---|---|
| 分面怎么落 | **沿用 `Document.type='memory'`，不新建表**；分面用 **content 内小节标题**（如 `## Decisions`）编码，**不用**多份 title | 现在已经是「一项目一 memory 文档」，新增六份文档会让 `get_project_memory` 的合并语义与 `limit=100` 截断变脆；小节标题是最小改动且人读友好 |
| 分面标识 | 解析 content 的 `## <Facet>` 小节；未知小节归入 `Notes`（不丢内容） | 向后兼容既有自由文本（无小节时代码视为 `Notes`） |
| 写入接口 | `append_agent_memory(project_id, content, facet=...)`；`facet` 缺省 = `Notes` | 旧调用零改动 |
| 读取接口 | `get_project_memory` 保留 `documents` + `combined`，新增 `facets: {Decisions: [...], ...}` | 旧调用方不报错 |
| 权威等级来源 | 由**写入方**标注（人经 MCP 写 = `human`，agent 写 = `agent`），不推断 | 推断不可靠 |

### 4.2 防腐策略

| 问题 | 策略 | 落地范围 |
|---|---|---|
| 重复追加同一结论 | 写入前**规范化去重**（NFKC + 大小写 + 空白/标点归一后比对） | Memory 分面 + `learnings` 写入面 |
| 失效结论越积越多 | 每条带 `superseded_by`；被取代的**标记而不删除** | 同上 |
| 权威等级冲突 | `human` > `agent`，冲突时高等级胜出并在返回里标明 | Memory 分面 |
| 无限增长 | Failed Attempts / Decisions 长期；一次性观察类定期归档 | 同上 |
| **语义级去重** | ⚠ **本变更为 spike，不进实现** —— 依赖 embedding / learning 引擎，仓库虽有 `episode_embedding` 但复用可行性未验证。字面归一去重先落地，语义去重单列决策 | spike |

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
