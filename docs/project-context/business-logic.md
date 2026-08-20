# AgentBoard 业务逻辑

> 完整需求见 `docs/requirements.md`（FR-1 ~ FR-18）。
> 战略基线见 AgentBoard MCP `project 3 → memory`。
> 本文档聚焦 **业务模型**（实体 + 关系 + 铁律）,非"功能清单"。

---

## 1. 一句话定位

> **"人 + 多个 AI Agent 共享同一任务事实源,把'写规范 → 生成任务 → Agent 执行 → 评审 → 完成'的闭环留在工具内。"**

AgentBoard 切入的缝隙：**项目管理工具与 AI Agent 之间的断层**。
Jira/Linear 是给人用的,Agent 无法原生读写任务；Agent 的上下文只在自己终端里。
AgentBoard 让"人 + 多个 Agent"共享同一任务事实源,并把"写规范 → 生成任务 → Agent 执行 → 评审 → 完成"的闭环留在工具内。

---

## 2. 三层价值主张

### 2.1 显性价值（用户立即感知）
- **MCP-first**：~130 个 MCP 工具,Codex/Claude/MiniMax/WorkBuddy 开箱即连
- **规范驱动**：内嵌 OpenSpec/Superpowers 方法论,spec-on-task
- **自动化闭环**：需求澄清（Proposal）→ Story → 开发认领 → 评审 → 完成,人工仅守关键闸门

### 2.2 隐性价值（运营一段时间后显化）
- **多 Agent 编排**：事件驱动认领/评审/接力,跨 Agent 协作可观察
- **Agent 能力评分**：每个 Agent 的成功率、平均时长、踩坑模式可视化
- **项目记忆沉淀**：playbook DB 级幂等,跨会话累积

### 2.3 护城河（数据积累型）
- **episode RAG**：每个任务执行的上下文向量化,跨项目检索复用
- **LLM-as-judge 评分**：任务结果自动评分 + 确定性降级
- **playbook 沉淀**：成功/踩坑模式自动结构化,越用越懂项目

> 这是**数据网络效应**：用户越多 → 数据越多 → Agent 越准 → 用户越留。

---

## 3. 核心能力（FR 摘要）

| FR | 模块 | 一句话 |
|---|---|---|
| FR-1 | 项目树管理 | Project → Epic → Story → Task/Bug 四级层级,固定不嵌套 |
| FR-2 | 工作项 | `task\|bug` + `status` 走状态机 |
| FR-3 | 描述与规范 | `description`(人读) + `spec`(OpenSpec/Superpowers 风格)双 markdown |
| FR-5 | 状态流转 | `backlog → todo → in_progress → in_review → done`,Bug 额外 `verifying` |
| FR-6 | MCP 能力 | ~130 工具,三端共享 service/DB 层 |
| FR-7 | OpenSpec 工作流 | spec 挂任务,`spec_proposal` 一键生成提案 |
| FR-11 | MCP 鉴权 | Bearer Token + Streamable HTTP,生产可用 |
| FR-13 | 优先级 | `highest\|high\|medium\|low\|lowest`,默认 medium |
| FR-14 | 评论 | 人 + Agent 共享评论流,不污染 description |
| FR-15 | 附件 | 10MB 限制,UUID 文件名,MIME 白名单 |
| FR-16 | Sprint | `planned\|active\|closed`,同项目最多一个 active |
| FR-17 | Agent 定时开发 | `AgentSchedule` (once/cron) + CAS 认领 + 幂等键 |
| FR-18 | 项目文档 | 独立 `Document` 实体,`memory\|plan\|knowledge\|design` 四类 |

> 完整字段、端点、迁移见 `docs/requirements.md`。

---

## 4. 目标用户分层

### T0：AI 原生独立开发者（核心早期用户）
- 画像：用 Codex/Claude Code/Cursor 大量产出代码的个人
- 痛点：任务分散在终端、IDE、记忆里,无统一进度源
- 价值：把"我说的事 → 任务清单 → 实际跑通"串起来
- 现状：107 用户 / 104 项目 / 479 任务 的本地实例即此类用法

### T1：AI 原生小团队（<50 人）
- 痛点：多个 Agent 抢任务 / 输出冲突 / 评审无标准
- 价值：Agent 编排 + 评审投票 + 接力工作流

### T2：传统团队的 AI 转型者
- 已有 Jira/Linear,想加 AI 能力
- 价值：渐进式接入（先 MCP,再自动化）

---

## 5. 数据模型（ER 概要）

```
Project (1) ──< (N) Epic
Epic    (1) ──< (N) Story
Story   (1) ──< (N) Task                          ← Task 不再嵌套
Project (1) ──< (N) Sprint ──< (N) Task
Task    (1) ──< (N) Comment / Attachment / AgentSchedule / AgentRun
Project (1) ──< (N) Document ──< (N) DocumentComment
Project (1) ──< (N) Proposal  ──< (N) ProposalRound ──< (N) ProposalQuestion
Project (1) ──< (N) Webhook / Notification / Member
```

### 关键不变量

| # | 不变量 | 违反后果 |
|---|---|---|
| 1 | **Task 不嵌套**：`story_id` 唯一指代,不可有 `parent_task_id` | 聚合查询与权限模型崩溃 |
| 2 | **Spec 是 markdown**：description/spec 都是自由 markdown,无固定 schema | 渲染器必须自己扛格式 |
| 3 | **状态机单向收敛**：`done` 不应回 `in_progress`（Bug 例外有 `verifying` 中转） | 数据语义模糊 |
| 4 | **API Key 只存 SHA-256 摘要**：DB 泄露不直接出 key | 不可逆哈希必须一次性 |
| 5 | **Agent 写路径只走 FastAPI**：.NET 端 Stage 0/1 永远只读 | 双栈一致性靠契约护栏 |
| 6 | **事件消息只带 ID**：状态/数据一律回查 DB | 消息体陈旧导致脏读 |
| 7 | **AgentSchedule 无候选 = 跳过**：不创建空 run | 噪声 + 误判 |
| 8 | **公开 REST 契约由 FastAPI 冻结**：.NET 端镜像,无擅自加端点 | 双栈漂移 → 雪崩 |

---

## 6. 三端共享模型

```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  FastAPI     │  │  .NET 10     │  │  MCP Server  │
│  (REST)      │  │  (BFF)       │  │  (stdio/HTTP)│
│  8000        │  │  18000       │  │  8001        │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                 │
       ▼                 ▼                 ▼
  ┌────────────────────────────────────────────┐
  │         features/* (service / DB)         │  ← 单一真源
  └────────────────────────────────────────────┘
                         │
                         ▼
                  ┌──────────────┐
                  │  SQLAlchemy  │
                  │  + Alembic   │
                  └──────┬───────┘
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
         SQLite(开发)         MariaDB(生产)
```

**业务铁律**：三端是 **transport**,不是 **logic**。所有业务规则都在 `features/*/service.py`。

---

## 7. 核心事件流

### 7.1 Proposal 流转（需求澄清 → 任务化）

```
queued  ── claim（CAS）──► analyzing
analyzing  ── ask（多轮）──► awaiting
awaiting  ── 用户答 ──► answered ── claim 再问 ──► analyzing
answered ×N ── finalize ──► converged
converged ── 人工终审 ──► ticket_created
```

### 7.2 Story 流转

```
backlog ── confirm(人工闸)──► confirmed
confirmed ── ready(开发认领)──► in_progress
in_progress ── submit(提交评审)──► in_review
in_review ── review(approve/reject)──► ready / in_progress(打回)
ready ×N ── review(approve)──► done
```

> 5 轮未收敛 → 护栏 `blocked`,等人工仲裁。

### 7.3 Agent 自动化（FR-17）

```
AgentSchedule (cron) ──► 调度器挑 todo eligible task ──► AgentRun 创建（CAS）
  └─► Worker claim（CAS + 租约 + 幂等键）──► 执行 ──► heartbeat ──► complete_run
  └─► 状态回写: pending → running → success / failed
  └─► 失败 5 轮 / 超时未决 → 扫描器自愈（重派 or 护栏 blocked）
```

---

## 8. 业务级红线（不可破坏）

1. **公开 REST 契约冻结**：URL / Method / Body Schema / Status Code / Error 格式（`{"detail":"..."}`）全部 1:1
2. **事件消息只带 ID**：不允许消息体携带业务快照
3. **Spec 自由 markdown**：无强制模板,但提供 OpenSpec 风格生成器
4. **Agent 评审可打回**：5 轮未收敛 → 护栏,不自动通过
5. **MCP / REST / Web 三端逻辑零分叉**：业务规则在 service,transport 不许复读
6. **.NET 端 Stage 0/1 不写**：只读连接 + AsNoTracking,schema 由 Alembic 管控
7. **生产环境 fail-fast**：`AGENTBOARD_SECRET ≥ 32B` / `REQUIRE_AUTH=1` / CORS 白名单 / HTTPS

---

## 9. 关键指标（运营视角）

| 指标 | 含义 | 现状基线 |
|---|---|---|
| 用户 / 项目 / 任务 | 内部 dogfooding 规模 | 107 / 104 / 479 |
| Agent 适配器数 | CLI/模型覆盖 | 4（codex/claude/minimax/workbuddy） |
| MCP 工具数 | Agent 原生能力面 | ~130 |
| 评审收敛率 | approve / total | 待埋点 |
| 任务自动化率 | schedule 触发 / 总完成 | 待埋点 |
| Playwright E2E 覆盖 | 真实浏览器断言 | Epic 149 8/8 + 多 Story |

---

## 10. 业务护城河

- **episode RAG**：每个任务执行的上下文向量化,跨项目检索复用
- **LLM-as-judge 评分**：自动评分 + 确定性降级,失败不阻断主链路
- **playbook 沉淀**：成功/踩坑模式结构化,DB 级幂等,跨会话累积
- **多 Agent 协作可观察**：评审、认领、接力事件全留痕

> 这些能力 **不直接产生收入**,但 **直接提高单 Agent 效率**,形成数据网络效应。

---

## 维护

- **真源**：AgentBoard MCP `project 3 → memory`
- **更新策略**：业务模型 / ER / 铁律 / 红线有变更时,先改 MCP memory,再同步本文件
- **下次评审**：每季度或产品定位有变时重审
