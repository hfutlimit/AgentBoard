# AgentBoard 项目上下文汇总

> 本目录是 **AgentBoard 项目记忆（项目级 + Agent 级）** 的本地落档副本。
> 一切以 AgentBoard MCP `project 3` 上的 [project memory](https://agentboard) 为权威来源；
> 本地副本用于新人上手、跨 Agent 上下文补齐、断网时只读参考。
>
> 汇总基线日期：2026-08-20

---

## 目录

| 文件 | 内容 | 适用读者 |
|---|---|---|
| [business-logic.md](./business-logic.md) | 业务模型、核心能力、目标用户、ER 数据模型、关键不变量 | 产品 / 后端 / 前端 / Agent |
| [long-term-roadmap.md](./long-term-roadmap.md) | 12 个月路线、四条商业化路径、关键决策待办、护城河 | 决策者 / 投资人 / 核心开发者 |
| [refactor-progress.md](./refactor-progress.md) | 后端 9 阶段重构、双栈 BFF 演进、前端布局重建、仓库清理、UI 风格轨道 | 后端 / 前端 / SRE / 任何要改代码的人 |
| [coding-conventions.md](./coding-conventions.md) | 迭代纪律、契约冻结、提交规范、测试规范、设计 token 约定 | 所有贡献者 / Agent |

---

## 核心一句话定位

> **"人 + 多个 AI Agent 共享同一任务事实源,把'写规范 → 生成任务 → Agent 执行 → 评审 → 完成'的闭环留在工具内。"**

不是"又一个 Jira"——是 **Jira + Agent-native 协作中枢**。

---

## 当前阶段

> **内部工具已验证、产品化未完成。**（2026-08-17 评审基线）

补完下面这四件事,就从"个人玩物"变成"团队标配"：

1. 把安全做对（P0 一天内）
2. 把架构做对（前端拆、测试跑绿）
3. 把定位说清（个人 vs 团队二选一）
4. 把护城河做厚（数据积累型能力持续投入）

---

## 相关原始材料

- 项目战略：`AgentBoard MCP → project 3 → memory`（产品定位、商业模式、护城河）
- 项目评审：`AgentBoard MCP → project 3 → knowledge/设计`（代码评审、安全威胁）
- 仓库一级文档：`docs/requirements.md` / `docs/tasks.md` / `docs/refactor-plan.md` / `docs/architecture-v2.md`
- 前端契约：`docs/design-prototypes/layout-rebuild/codex/MIGRATION.md`
- 仓库清理：`docs/superpowers/plans/2026-08-19-repository-cleanup.md`

---

*本目录任何文件变更后,请同步更新 AgentBoard MCP 项目记忆（见各文件末尾"维护"段）。*
