# Tasks — Agent Handoff & Project Memory

**status**: draft
**date**: 2026-09-14
**Epic**: AgentBoard Epic 169（project 3）
**承接**: Epic 168（恢复侧）的 story 453「换 agent 的上下文交接」

> 每个 slice 独立 commit；落地后追加 `tests/e2e/dod_registry.py` + 更新 `docs/e2e-plan.md`。

---

## Slice 1: Project Memory 分面结构化（Epic 169 / Story 455）

**目标**：把 `Document.type='memory'` 的自由文本追加升级为分面结构，向后兼容。

- [ ] `features/documents/models.py`：确认分面落库方案（title 前缀 vs content 内小节 vs 新增列），**优先不改表**
- [ ] `features/mcp/documents.py`：`_memory_title(facet=)` 口径 + 常量唯一真源
- [ ] `mcp_server.py::append_agent_memory`：新增 `facet` 可选入参（缺省走兼容自由文本面）
- [ ] `mcp_server.py::get_project_memory`：返回结构向后兼容（保留 `documents` + `combined`），新增分面视图
- [ ] 单测：五面各自可写可读
- [ ] 单测：**不带 facet 的旧调用行为完全不变**（回归保护）
- [ ] 文档：每面一个**真实例子**（禁止空白模板）

**commit**: `feat(memory): facet the project memory into five sections`

---

## Slice 2: Memory 防腐（Story 456）

**目标**：正面回答外部 review 的「如何避免变成垃圾文档」。

- [ ] 写入前规范化去重（大小写 / 空白 / 标点归一）
- [ ] 语义级去重的可行性判定（仓库已有 `episode_embedding` + learning 检索引擎，评估复用；不可用则**显式声明只做字面级**）
- [ ] `supersedes` / `superseded_by` 关系：被取代的记忆**标记不删除**
- [ ] `get_project_memory` 默认不返回已 superseded 条目；提供显式历史查询
- [ ] 来源等级（人写 > agent 推断 > 单次观察）+ 冲突时的胜出与标注
- [ ] 分面保留策略（Decisions / Failed Attempts 长期；一次性观察归档）
- [ ] 单测：连续追加同一结论 3 次 → 只留 1 条

**commit**: `feat(memory): dedupe, supersede and grade project memory`

---

## Slice 3: Handoff Package 契约（Story 457）

**目标**：结构化交接产物 + 新 agent 强制加载。

- [ ] 定义 handoff 产物载体（建议 task 级）与 JSON 结构：`status / completed / remaining / known_issues / changed_files / next_steps / verification`
- [ ] 生成时机接入：`soft_takeover_run`、任务人工退回 todo、review 驳回导致换 agent
- [ ] 生成**幂等**（同一 run 重复生成不重复创建）
- [ ] 加载契约：新 agent 上下文组装时强制带上 handoff（若存在）
- [ ] 与 Epic 168 / Story 453 的恢复动作对齐接口（判死 → release lease → 换 agent → 写 handoff）
- [ ] 单测：模拟 A 死亡 → B 接手 → 断言 B 的上下文含 handoff **每个字段**
- [ ] 单测：评论 / spec / 部分提交不丢（现有行为不得回归）

**commit**: `feat(handoff): structured handoff package with mandatory load`

---

## Slice 4: Agent Knowledge Stream（Story 458）

**目标**：第二条流落地，与 `workflow_run_events` 严格分离。

- [ ] 确定载体（建议新表 `agent_knowledge_entries`，或复用 `Document.type='knowledge'`）
- [ ] 五类事件：`analysis` / `decision` / `discovery` / `failure` / `handoff`
- [ ] 写入入口（REST + MCP），可追溯到 attempt
- [ ] 与 Project Memory 的沉淀链路（知识条目 → 对应记忆面）
- [ ] 单测：**写入知识条目不影响 `workflow_run_events` 行数与 timeline 查询耗时**
- [ ] 保留期与 Epic 168 / Story 451 的 activity stream **分别定义**（知识长期、activity 短周期）

**commit**: `feat(memory): add the agent knowledge stream`

---

## Slice 5: Entity 边界与并行策略（Story 459）

**目标**：五个对象定义冻结 + 并发受理按隔离判定。

- [ ] 文档：Agent / Worker / AgentRun / Attempt / Memory 五者职责写清楚
- [ ] 命名收敛：对外「attempt」**只指一次 AgentRun**；`TaskAssignment` = 调度关系，`MessageAttempt` = 投递重试
- [ ] 受理并发时按 workspace 隔离度判定（共享 `existing_checkout` → 拒绝，返回结构化 reason）
- [ ] 单测：共享 checkout 下并发受理被拒且原因可读
- [ ] 单测：`worktree` / `fresh_clone` 下并发受理**不被误拒**
- [ ] 明确不新增 attempt 表、不做表合并迁移

**commit**: `docs(runtime): freeze the five runtime entities and the isolation-based fan-out rule`

---

## 跨切片任务

- [ ] `openspec/changes/agent-handoff-and-project-memory-20260914/` 三件套已就位
- [ ] 每 slice 完成后：`git add` + commit + push（无需确认）
- [ ] 追加 `tests/e2e/dod_registry.py` 条目
- [ ] 更新 `docs/e2e-plan.md`
- [ ] 与 Epic 168 的依赖顺序：**456 与 455 同批**（结构与防腐要一起定，否则只是把垃圾分类）；457 依赖 450（progress report 流）提供交接素材；458 依赖 455

---

## 完成定义（DOD）

- 新 agent 接手一个失败 task 时，能在**不看聊天记录**的前提下读到：做到了哪 / 没做到哪 / 踩过什么坑 / 改了哪些文件 / 下一步做什么
- 项目记忆在连续 50 次追加后仍可读（防腐生效，不是越长越糊）
- 知识流写入不影响 workflow timeline 的性能指标
- 五面记忆里每面至少有一个来自真实历史的例子
