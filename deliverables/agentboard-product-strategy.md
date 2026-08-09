# AgentBoard 产品策略备忘录：如何突出核心功能、打造特色、让人爱上它

> 基于代码库现状盘点（README / docs/requirements.md / openspec/spec.md / agentboard/scheduler.py / mcp_server.py）撰写。
> 结论先行：**AgentBoard 的真正护城河不是"轻量 Jira"，而是"Agent 原生的项目管理系统"**——让 AI Agent 成为一等公民，拥有身份、记忆与自主执行闭环。当前最大的战略缺口是：调度器只"造单"，没有"执行器"。

---

## 0. 一句话定位（建议改成这样）

> **AgentBoard = 人和开发 Agent 共同工作的项目中枢。**
> 人在这里写意图（spec），Agent 在这里认领、执行、回写；团队记忆沉淀为可复用的知识。

不要再主打"轻量 Jira"。Jira 平价是入场券，不是卖点。主打"**你的 AI Agent 真正在用的项目管理工具**"——这是 Jira / Linear / GitHub Projects 都没占据的心智空位。

---

## 1. 现状盘点（基于代码，非臆测）

| 能力 | 状态 | 证据 |
|------|------|------|
| 层级 PM（Project→Epic→Story→Task/Bug） | ✅ 完整 | models.py / api.py / Web SPA |
| 任务 spec（markdown 规范）+ 双向生成 | ✅ 完整 | `spec_proposal` / `generate_tasks_from_spec`（spec.md） |
| MCP 服务（stdio + Streamable HTTP + Bearer） | ✅ 完整 | mcp_server.py，40+ 工具 |
| 优先级 / 评论 / 附件 / Sprint | ✅ 已落地 | FR-13~16 |
| 文档模块（memory/plan/knowledge/design + 评审状态机） | ✅ 完整 | FR-18，独立 `documents` 表 |
| **AgentSchedule 调度扫描器** | ✅ 扫描+造单 | scheduler.py：行锁 lease、幂等键、创建 `pending` AgentRun |
| **AgentRun 执行器（真正跑 Agent 的适配器）** | ❌ **缺失** | scheduler.py 中无 subprocess/httpx/codex/cursor 调用；FR-17 注明"待实现" |
| Agent 记忆（跨会话持久化、自动加载） | ⚠️ 仅雏形 | 仅 `Document.type=memory` 文本载体，无"Agent 启动即加载记忆"的 MCP 机制 |

**核心结论**：自主开发闭环的"触发侧"已就绪，"执行侧"是真空。没有执行器，"让 agent 用自动化任务自主开发"目前是**架构承诺而非可用能力**。

---

## 2. 如何突出核心功能（先讲清楚，再做得显眼）

### 2.1 杀死"功能清单式"叙事，改讲"闭环故事"
README 现在把 MCP 埋在第 3 节、把"Agent 闭环"散落在 FR-17。建议把首屏改成一条**可视化闭环**：

```
人写 spec ──▶ AgentBoard 生成任务 ──▶ Agent 认领(MCP)
     ▲                                              │
     │                                              ▼
人评审/回滚 ◀── Agent 回写状态+评论+spec ◀── Agent 执行
```

这条线就是你的"英雄旅程"，比任何功能表都好记。

### 2.2 给 Agent 一个"看得见的工位"
现在 Agent 的活动藏在 task 评论里。建议新增 **Agent 活动视图 / Agent Inbox**：
- 实时流：哪个 Agent 认领了什么、正在跑、刚写完、被驳回
- 每个 Agent 一张"工牌"：身份、最近运行、成功率、擅长领域
- 让"Agent 在干活"这件事**可见、可感知**——可爱度来自"它在替我做事"的踏实感

### 2.3 把 MCP 从"附录"提到"门面"
MCP 是你的分发渠道，不是技术细节。README 首屏应直接给：
- `docker compose up` 一条命令拉起
- 复制即用的 Claude / Codex / WorkBuddy / Cursor 配置片段（examples/ 已有雏形，提到最上面）
- 一段 90 秒录屏：Agent 自己从 spec 建任务并改状态

---

## 3. 特色功能建议（按"防御性 + 吸引力"排序）

### 🥇 优先级 1：补齐 AgentRun 执行器（不是特色，是生死线）
先把第 1 节的真空填上，否则其它都是空中楼阁。
- 设计**命令模板 / 适配器**：`AgentRun → 调起指定 Agent（Codex/WorkBuddy/Cursor…）→ 传入 task spec 作为上下文 → 捕获输出 → 回写 status/comment/spec → 标记 success/failed`
- 复用已就绪的 lease + 幂等键，避免重复触发
- 默认**不自动 push/merge**（FR-17 已规定），信任来自可控
- 这一步做完，"自主开发"才从 PPT 变成真功能

### 🥈 优先级 2：把"Agent 记忆"做成签名功能（真正的差异化）
当前 `memory` 只是文档的一个类型。要升维为 **Agent 的跨会话大脑**：
- 新增 MCP 工具 `get_project_memory` / `append_agent_memory`：Agent 每次会话开始**自动加载**项目记忆（约定、踩坑、用户偏好）
- 记忆分层：项目级（团队规范）+ Agent 级（某 Agent 的个性/擅长）+ 任务级（spec 已是）
- 这直接对标 Mem0 / Zep，但**长在 PM 里、和任务闭环打通**——这是别人没有的组合
- 卖点话术："让 Agent 越用越懂你的项目"

### 🥉 优先级 3：人在回路的 AgentRun 审查台（建立信任）
自主 ≠ 失控。没有这个，没人敢真让 Agent 跑：
- AgentRun 详情页：diff 视图（它改了哪些文件/任务）、一键 approve / reject / 回滚
- 跑挂了有日志引用、有 summary，人 30 秒定性
- 信任是"让人喜欢"的前提

### 加分项（吸引力 / 传播性）
4. **Agent 花名册（Roster）**：把接进来的 Agent 列成"机器人团队页"，带头像、战绩、擅长标签——可爱、有分享欲。
5. **对话→spec 自动起草**：粘贴会议/IM 记录，Agent 产出 spec + 子任务清单。降低"写规范"的摩擦力。
6. **Agent 战绩榜 / 遥测**：谁关任务最多、最快、最少回滚。轻量 gamification，社区爱晒。
7. **Spec 模板市集**：可分享的 OpenSpec 模板（"新功能""修 bug""重构"），降低从零开始成本。
8. **浏览器内 MCP Playground**：不用装客户端就能试 MCP 工具，降低试用门槛。

---

## 4. 让人喜欢（ adoption & love ）

- **开发者体验优先**：`docker compose up` 一键起 + **预置一个 demo 项目**，里面有个 Agent 你能实时看它干活。第一次打开就有"哇"时刻。
- **开源 + 干净 README + GIF**：在 GitHub 放清晰首图、闭环动图、"Works with Claude / Codex / WorkBuddy / Cursor"徽章。
- **狗粮自己吃**：AgentBoard 自己的 backlog 就用 AgentBoard + Agent 管（overview.md 已见雏形）。把"我们用自己的产品养 Agent"做成招牌故事。
- **降摩擦三件套**：模板化 spec、chat→task、Agent 自动加载记忆——让人"几乎不费劲"就能把 Agent 接进来。
- **社区钩子**：展示"Agent 帮我写完的 PR"，降低他人尝试的心理门槛。

---

## 5. 风险与诚实提醒

| 风险 | 说明 | 应对 |
|------|------|------|
| 执行器缺失 | 自主闭环只完成触发侧 | 见 §3 优先级 1，先补这个再谈其它 |
| 记忆仅文本雏形 | "Agent 记忆"目前名不副实 | 升维为自动加载的 MCP 记忆层（§3 P2） |
| Jira 平价比拼是陷阱 | 永远拼不过 Jira 的生态 | 不卷 parity，卷 agent-native 空位 |
| 自主执行的信任成本 | 人不敢放手 | HITL 审查台（§3 P3）是前置条件 |

---

## 6. 建议路线图（90 天）

**Phase 1（第 1–3 周）· 把承诺变成能力**
- 实现 AgentRun 执行器适配器（命令模板 + 输出回写 + 不自动 push）
- Agent Inbox / 活动流 MVP（让"Agent 在干活"可见）

**Phase 2（第 4–6 周）· 签名记忆功能**
- `get_project_memory` / `append_agent_memory` MCP 工具
- Agent 会话启动自动加载项目记忆
- HITL 审查台（AgentRun diff + 批准/回滚）

**Phase 3（第 7–10 周）· 可爱 & 传播**
- Agent 花名册、战绩榜
- chat→spec 起草
- 浏览器内 MCP Playground

**Phase 4（第 11–12 周）· 开源与增长**
- GitHub 仓库整理、README 首图+动图、配置片段上移到首屏
- 预置 demo 项目 + 一键 `docker compose up`
- 对外讲"我们用自己的 Agent 养 AgentBoard"的故事

---

*附：本文所有功能状态均来自对当前代码库的直接核查（scheduler.py / mcp_server.py / spec.md / requirements.md）。未实现的均以"缺失/雏形"明确标注，未做外部市场数据引用。*
