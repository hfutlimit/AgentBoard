# Agent Handoff & Project Memory — 变更提案

**status**: draft
**date**: 2026-09-14
**来源**: 2026-09-14 外部架构 review 第 4-5 轮（handoff / shared memory）
**前置**: `workflow-run-overview-20260911`（WorkflowRun slice 1+2 已落地）
**关联**: AgentBoard Epic 168（Agent Runtime Contract & Workflow Reconciler）、Epic 169（本变更）

---

## 为什么做

前面三轮解决的是「agent 活着吗 / 在干什么 / 挂了怎么办」。这一轮解决更难的一层：

> **Agent A 做了一半，Agent B 如何无缝接手？**

这不是重新分配任务。AI coding 里 A 已经产生了大量不可重建的上下文（分析过项目结构、读过 20 个文件、试过 3 种方案、发现过迁移坑、改了半个文件、测试失败）。B 从零开始等于重复 token、重复探索、重复踩坑。

**现状盘点（已用代码核实，不是"从零建"）：**

| 能力 | 现状 | 位置 |
|---|---|---|
| 项目记忆载体 | **已有**：`Document.type='memory'`，两层（项目级 `title="项目记忆"` / Agent 级 `title="Agent 记忆 · {agent}"`） | `features/documents/models.py:24,86` |
| 会话启动自动加载 | **已有**：`get_project_memory(project_id, agent)` 返回 documents + combined 文本 | `mcp_server.py:1313` |
| 会话中沉淀 | **已有**：`append_agent_memory(project_id, content, agent)`，同名续写幂等累积 | `mcp_server.py:1337` |
| 接管时保留上下文 | **已有**：`soft_takeover_run` 保留评论 / spec / 部分提交；`last_progress_note` 设计意图就是给接手者看 | `scheduling/service.py:2981, 2810` |
| 进度历史 | **缺**：只有覆盖式单字段 `last_progress_at` / `last_progress_note`(500) | `scheduling/models.py:69-75` |
| 记忆结构 | **缺**：纯自由文本 `old + "\n\n" + content` 无限追加 → 无分面、无去重、无时效、无淘汰 | `mcp_server.py:1350` |
| 交接产物 | **缺**：没有结构化 handoff package，B 只能盲读评论流 | — |
| 知识流 | **缺**：只有 `workflow_run_events`（状态轴），经验类信息无归属 | — |

结论：**记忆的"骨架"已经长好了（Epic 78 Story 107「跨会话大脑」，注释自称对标 Mem0/Zep），缺的是"结构 + 防腐 + 交接契约"。** 本变更做的是升级，不是新建。

## 改什么

1. **Project Memory 分面化**：把自由文本追加升级为六类结构（Decisions / Architecture / Known Issues / Failed Attempts / Coding Rules / Handoff），保持 `get_project_memory` 向后兼容。
2. **Memory 防腐**：去重 / 时效 / 权威等级 / 淘汰 —— 直接回答「如何避免变成垃圾文档」。
3. **Handoff Package 契约**：结构化交接产物（status / completed / remaining / known_issues / changed_files / next_steps）+ 生成时机 + 新 agent 强制加载。
4. **Agent Knowledge Stream**：与 `workflow_run_events` **分离**的第二条流（analysis / decision / discovery / failure / handoff）。
5. **Entity 边界与并行策略**：明确 Agent / Worker / AgentRun / Attempt / Memory 五个对象的边界；并行策略沿用现有「按 workspace 隔离」不变式，不改成「按 agent 串行」。

## 影响范围

`features/documents`（memory 结构）、`features/mcp/documents.py`（MCP 面）、`processors/maintenance.py` + `processors/coordinator.py`（handoff 生成时机）、新迁移（分面结构如果需要落库）、`docs/` 与 openspec 文档。

## 不做什么（已论证，防止后面被重新推翻）

- **不新建记忆系统**：`Document.type=memory` + `get/append_agent_memory` 已存在，本变更只做结构升级与防腐。
- **不把知识流塞进 `workflow_run_events`**：那张表是 19 类的**校验枚举**（`EVENT_TYPE_REQUIRED_KEYS`），知识是开放集合；且量级与保留期不同，会劣化 slice 5 的 `ix_workflow_events_run_id_desc`。
- **不改 WorkflowRun 层级**：`workflow-run-overview-20260911/tasks.md` 决策 3 已冻结 `Story → WorkflowRun → AgentRun(attempt)`，跨 run 重试靠 `reopened_from_run_id`。外部 review 提的 `Task → TaskExecution → WorkflowRun` 是反向的。
- **不禁止一个 agent 并行多 task**：现有不变式是**按 workspace 隔离**（`processors/workspace/models.py:19-21` `FORCED_MAX_PARALLEL`：`existing_checkout` 强制 `max_parallel=1`，因为"并行任务必然互相污染"）。直接按 agent 串行会误禁 `worktree` / `fresh_clone` 下**已经隔离**的安全并行。

## 成功判据

- 新 agent 接手一个失败 task 时，能在**不看聊天记录**的前提下读到：做到了哪、没做到哪、踩过什么坑、改了哪些文件、下一步做什么。
- 项目记忆在连续 50 次追加后仍然可读（有防腐策略，不是越长越糊）。
