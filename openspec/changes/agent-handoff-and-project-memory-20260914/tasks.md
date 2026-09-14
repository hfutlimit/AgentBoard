# Tasks — Agent Handoff & Project Memory

**status**: draft
**date**: 2026-09-14
**Epic**: AgentBoard Epic 169（project 3）
**承接**: Epic 168（恢复侧）的 story 453
**设计真源**: 本目录 `design.md`（含 §2.5 与 `learnings` 的边界）
**执行纪律**: 设计评审的 4 个 P0 冻结前不领 455–459。

---

## Slice 1: Project Memory 分面结构化（story 455）

**目标**：把 `Document.type='memory'` 的自由文本追加升级为**五面**，向后兼容。**存储方案已在 design §4.1 冻结**（content 内 `## <Facet>` 小节，不新建表、不多份 title）。

- [ ] `features/mcp/documents.py`：分面常量 + 小节解析器（未知小节归 `Notes`，不丢内容）
- [ ] `mcp_server.py::append_agent_memory`：新增可选 `facet`（缺省 = `Notes`）
- [ ] `mcp_server.py::get_project_memory`：保留 `documents` + `combined`，新增 `facets` 视图
- [ ] 单测：五面各自可写可读；**不带 facet 的旧调用行为完全不变**
- [ ] 单测：无小节的存量自由文本被解析为 `Notes`（向后兼容）
- [ ] 文档：每面一个真实例子（禁止空白模板）

**commit**: `feat(memory): facet the project memory into five sections`

---

## Slice 2: Memory 防腐（story 456）

**目标**：正面回答「如何避免变成垃圾文档」。**落地范围含 `learnings` 写入面**（design §2.5 冻结决定 3）。

- [ ] 写入前**规范化去重**（NFKC + 大小写 + 空白/标点归一）—— Memory 分面 + `learnings` 写入面
- [ ] `superseded_by` 关系：被取代**标记不删除**（审计保留）
- [ ] `get_project_memory` 默认不返回已 superseded；提供显式历史查询
- [ ] 来源等级（`human` > `agent`，由写入方标注，**不推断**）
- [ ] 保留策略：Decisions / Failed Attempts 长期；一次性观察归档
- [ ] 单测：连续追加同一结论 3 次 → 只留 1 条
- [ ] ⚠ **语义级去重单列 spike**（依赖 embedding / learning 引擎复用可行性未验证），本 slice **不做**

**commit**: `feat(memory): dedupe, supersede and grade project memory`

---

## Slice 3: Handoff Package（story 457）

**目标**：交接产物的**结构与加载契约**。⚠ **产物 schema 只在本 story 定义** —— 453 只留恢复动作（判死 → 放租约 → 调本 story 的生成入口），不得再定义 schema 或读取契约。

- [ ] 定义 handoff 实体与 JSON 结构：`status` / `completed` / `remaining` / `known_issues` / `changed_files` / `next_steps` / `verification`
- [ ] 生成入口**幂等**（同一 run 重复生成不重复创建）
- [ ] 生成时机由 **453 触发**（本 story 提供入口，不自己扫描）
- [ ] 加载契约按 design §3 表：**强制** = handoff（按 `task_id` 取最新）+ `get_project_memory`；**可选** = `git diff`（相对 story 459 记录的**认领基线 commit**）+ 测试结果（取 handoff 的 `verification`，其次 activity 最后一条测试命令，取不到显式标「无测试证据」）
- [ ] 单测：A 死亡 → B 接手 → 断言 B 上下文含 handoff **每个字段**
- [ ] 单测：无基线 commit 时**不给 diff**（不得猜）
- [ ] 单测：评论 / spec / 部分提交不丢（现有行为不回归）

**commit**: `feat(handoff): define the handoff package and its load contract`

---

## Slice 4: Knowledge 写入面与晋升通道（story 458）

**目标**：**复用既有 `learnings` 表**，不新建 `agent_knowledge_entries`。定义「activity / 分析 → learnings → Memory 分面」的晋升链（design §2 / §2.5）。

- [ ] 写入面：把一次分析 / 决策 / 失败**晋升为 `learnings` 条目**（用既有 5 类 category，不造第二套分类）
- [ ] 晋升映射：`project_convention` → **Coding Rules** 面；`execution_failure` → **Failed Attempts** 面
- [ ] 晋升到 Memory 分面时记 `source_run_id`（可追溯到 attempt）
- [ ] 明确 **activity 原文不进 knowledge**（design §2 冻结）
- [ ] 单测：写入 knowledge **不影响** `workflow_run_events` 行数与 timeline 查询耗时
- [ ] 单测：晋升链端到端（activity/分析 → learnings → 分面）
- [ ] ⚠ 保留策略与 story 451 的 activity **分别定义**（knowledge 晋升后长期，activity 短留）

**commit**: `feat(learning): route knowledge promotion through the existing learnings table`

---

## Slice 5: Entity 边界与并行策略（story 459）

**目标**：五个对象定义冻结 + 并发受理按隔离判定。**设计评审已认定可提前，不挡记忆切片。**

- [ ] 文档：Agent / Worker / AgentRun / Attempt / Memory 五者职责（尤其「attempt 不指三种表」）
- [ ] 命名收敛：`TaskAssignment` = 调度关系，`MessageAttempt` = 投递重试，**对外不再叫 attempt**
- [ ] 受理并发按 workspace 隔离度判定（共享 `existing_checkout` → 拒绝 + 结构化 reason）
- [ ] 记录 **认领基线 commit**（供 story 457 的 `git diff` 使用）
- [ ] 单测：共享 checkout 下并发受理被拒且原因可读
- [ ] 单测：`worktree` / `fresh_clone` 下并发受理**不被误拒**
- [ ] 不新增 attempt 表、不做表合并迁移

**commit**: `docs(runtime): freeze the five runtime entities and the isolation-based fan-out rule`

---

## 跨切片任务

- [ ] 三件套已就位；Epic 168 的设计在 `openspec/changes/agent-runtime-contract-and-reconciler-20260914/`
- [ ] 每 slice 完成后：commit + push；追加 `tests/e2e/dod_registry.py`；更新 `docs/e2e-plan.md` **相关**章节（不是固定 section 14）
- [ ] 前置设计引用：MCP **doc 11**（Epic 78「Agent 记忆升维方案」）—— 只升级不重造；其 `get_agent_memory` 已漂移，见 errata

## 依赖顺序（按接口，不按 story 号）

```
455 + 456 必须同批（结构与防腐分开做 = 把垃圾分类）
   ↓
458（依赖分面口径）       459（文档 + 一条受理闸门，可提前，不挡记忆切片）
   ↓
457（依赖 168 的 450 提供素材 + 453 提供触发）
```

## 完成定义（DOD）

- 新 agent 接手失败 task 时，**不看聊天记录**就能读到：做到哪 / 没做到哪 / 踩过什么坑 / 改了哪些文件 / 下一步做什么
- 项目记忆连续 50 次追加后仍可读（防腐生效）
- **没有出现第三套记忆存储**（knowledge 走 `learnings`，人读走 Memory 分面）
- 五面每面至少一个来自真实历史的例子
