# Proposal → Ticket 异步转化流程 · 需求确认与设计要点

> 状态：需求已确认（2026-08-08，基于 Epic 96 Proposal 澄清回路扩展）
> 前置文档：`docs/agent-collaboration-requirements.md`（Epic 122，文档 #50）
> 关联实现：`agentboard/worker.py`（ProposalWorker + SubprocessAgentInvoker）、
> `agentboard/mq.py`（workflow 事件总线）、`agentboard/service.py`（convert_proposal_to_story）
> AgentBoard 同步（2026-08-08，经 MCP）：已创建文档 #59（design，Epic 96）承载本需求确认；
> Story 157（P3 定稿→Story/Task 自动生成）与 Story 154（P0 状态机）描述已追加需求扩展段落。

---

## 1. 背景

Epic 96 已交付 Proposal 澄清闭环：用户创建 proposal → Worker（CLI 拉起 agent）反复 grill
（open question 经 MCP 提问、用户前端回复）→ 收敛（converged）→ 人工点击「生成 Story」
→ 同步 REST 直建 Story + 子 Task，回填 `proposal.story_id`，终态 `story_created`。

**现状缺口**（与用户期望流程的差异）：

| 环节 | 现状 | 用户期望 |
|---|---|---|
| 生成 ticket 类型 | 仅 Story（必挂 epic） | **epic / story / task / bug 四选一** |
| 触发链路 | 点击按钮 → 同步 REST 直建 | 点击按钮 → **MQ 消息 → worker → CLI agent → MCP 生成 ticket** |
| convert 后 MQ 事件 | 无 | 新增 `proposal.ticket_requested` / `ticket_created` 通知 |
| 生成中间态 | 无（同步直建） | 新增 **`ticket_preparing`**（异步生成中，创建 ticket 是异步的） |
| 终态与回填 | `story_created` + `story_id` | 泛化 **`ticket_created`** + `ticket_type`/`ticket_id` |

## 2. 确认后的端到端流程

```
[1] 用户创建 proposal（REST + 前端工作台）                     【已有】
[2] 系统生成 MQ 消息，worker 消费（未配置 MQ 回退 DB 轮询）    【已有】
[3] worker 通过 CLI 拉起本机 agent（WorkBuddy/Claude Code/Codex，
    SubprocessAgentInvoker，prompt 走 stdin）                 【已有】
[4] agent 执行时经 MCP 提供 open question（proposal_ask 工具）  【已有】
[5] 用户回复 → answered → 重新进入下一轮（worker 重新消费）     【已有】
[6] 反复 grill 直至无 open question → converged               【已有】
[7] 用户点击「生成 ticket」（前端四选一 epic/story/task/bug
    + 父级选择），POST 转换请求                               【新增】
[8] 服务端校验（converged + 层级合法）→ 创建/复用转换记录（pending）
    → proposal 状态置 **ticket_preparing**（工单生成中）
    → 发 MQ 消息 proposal.ticket_requested → 返回 202         【新增】
[9] worker 消费 ticket_requested → 全量重放上下文 → CLI 拉起
    agent → prompt 指示 agent 用 MCP 创建 ticket              【新增】
[10] agent 经 MCP 调用专用工具创建实体（epic/story/task/bug）
     → 服务端事务内校验 + 创建 + 回填 proposal.ticket_type/
     ticket_id + 状态 ticket_preparing → **ticket_created**   【新增】
[11] 服务端发 proposal.ticket_created 事件（含 ticket 类型+id），
     供 workflow_worker / 各 agent 定向队列接力                【新增】
[10'] 失败路径：agent 执行失败/超时 → 转换记录置 failed，
      proposal 状态**回退 clarified**（保留失败原因，可重试）   【新增】
```

**人工闸门语义变化**：现有 convert 注释「保留人类最后一道闸——不直接由
Worker 调 create_story」改为「**人类触发（点击按钮）保留，创建动作异步委托
agent**」。闸门在 [7] 的点击动作上，不再在 [8]-[10] 的执行链路上。

## 3. 已确认的四个决策

1. **转换链路**：改为**异步 MQ 链路**（[8]-[10]），替代同步 REST 直建；
2. **Ticket 层级**：epic 独立；story 必挂 epic；task/bug 复用现有 task 表
   （type 区分 bug），**必挂 story**；
3. **MQ 事件**：新增 `proposal.ticket_requested`（worker 消费）与
   `proposal.ticket_created`（通知接力）；
4. **状态机**：`converged → ticket_preparing → ticket_created` 三段式——
   点击生成后先置 `ticket_preparing`（异步生成中），agent 创建成功后
   置 `ticket_created`（终态）；失败回退 `clarified` 可重试。回填通用
   `ticket_type` + `ticket_id`（兼容保留 `story_id`，视为 type=story 的快捷字段）。

## 4. 关键设计要点

### 4.0 Proposal 状态机（推荐命名，6 态）

| 中文名 | 英文枚举值 | 触发时机 | 说明 |
|---|---|---|---|
| 待开始 | `pending` | 创建/编辑后停留 | 点击「开始 grill」才入队发消息（新增） |
| 澄清中 | `grilling` | 点「开始 grill」/ 用户回复后 | 现有 queued/analyzing/answered 的对外合并展示 |
| 等待用户确认 | `waiting_user` | agent 提交 open question | 现有 awaiting |
| 需求已明确 | `clarified` | agent 确认无 open question | 现有 converged（展示名） |
| 工单生成中 | `ticket_preparing` | 点击「生成 ticket」→ 校验通过 | 对应 ticket_request 记录 processing；**创建 ticket 是异步的** |
| 已生成工单 | `ticket_created` | agent 经 MCP 创建成功后 | 终态；现有 story_created 泛化 |

流转：`pending → grilling ⇄ waiting_user → clarified → ticket_preparing
→ ticket_created`（ticket_preparing 失败回退 clarified 可重试）。
用户编辑非终态内容 → 回 `pending`（已答历史保留，全量重放）。
落地建议：枚举仅新增 `pending` / `ticket_preparing`，其余靠展示层映射，Worker
认领逻辑（claim queued/answered）零改动。

### 4.1 状态机与数据模型（迁移）

- `ProposalStatus` 新增 `TICKET_PREPARING`、`TICKET_CREATED`；旧 `STORY_CREATED`
  保留（历史数据兼容，建议迁移将存量 `story_created` 重写为 `ticket_created` +
  `ticket_type='story'` + `ticket_id=story_id`，或仅做展示层兼容二选一）；
- `ticket_preparing` ↔ `proposal_ticket_requests.status=processing` 联动：
  请求置 pending 时 proposal 置 ticket_preparing；请求置 done 时 proposal 置
  ticket_created；请求置 failed 时 proposal 回退 clarified；
- proposals 表新增 `ticket_type VARCHAR(20)` + `ticket_id INTEGER`（FK 无——四类实体
  不同表，靠类型+id 解析，与 comments 三实体「恰一非空」模式同构）；
- `story_id` 字段保留：创建 story 类 ticket 时同时回填，避免破坏现有查询
  （`GET /api/proposals/{pid}` 返回、proposal convert 幂等判断）。

### 4.2 幂等与防重放（沿用 P1/P2 既有铁律）

- 转换请求记录（新表 `proposal_ticket_requests`：proposal_id + type + parent_id +
  status(pending/processing/done/failed)），`(proposal_id, type)` 唯一约束，
  重复提交复用既有记录；
- agent 执行侧幂等：`proposal.ticket_id` 已回填且实体存在 → 直接返回，不重复创建
  （与现有 `convert_proposal_to_story` 幂等逻辑一致）；
- 消息 at-least-once：worker 重投不产生重复轮次/重复 ticket。

### 4.3 worker 与 agent 交互

- **复用 `SubprocessAgentInvoker`**（worker.py 已有，WorkBuddy/Claude Code/Codex，
  stdin 喂 prompt、stdout 解析决策 JSON）；
- agent 侧必须有 AgentBoard MCP 连接（外部环境依赖，同 Epic 96 澄清轮次前提）；
- **新增专用 MCP 工具 `proposal_create_ticket`**（参数：proposal_id、type、
  epic_id?、story_id?、title?）——服务端事务内完成「层级校验 + 创建 + 回填 +
  状态机推进」，避免 agent 拆多个工具调用导致的部分成功；
- 不推荐复用 `convert` REST：其语义是同步人工终审，异步链路下状态机与回填
  时机不同，双语义易混淆。

### 4.4 失败兜底

- ticket_request 超时（如 15min）未完成 → worker 重试（指数退避）或置 failed
  转人工（复用 proposal 租约回收模式 `_reclaim_stale`）；**proposal 状态回退
  clarified**，前端工作台显示「生成失败，可重试或转人工」；
- agent 退出码非 0 / 无有效决策 JSON → 记录 error_message，请求置 failed，
  前端工作台显示「生成失败，可重试或转人工」；
- 人工兜底：保留原同步 convert 端点作为管理员通道（可选）。

### 4.5 权限（受 `project_access_middleware` 约束）

- 转换请求创建：提案项目成员（私有项目）/ 管理员；
- agent 经 MCP 创建 ticket：以 agent 绑定的 `abk_` API Key 身份归属
  （同 Epic 122 agents 表 auth_key 语义），须有目标项目写权限；
- 校验 ticket 层级归属：epic ∈ 提案项目；story ∈ 指定 epic；task/bug ∈ 指定 story。

### 4.6 前端

- 工作台「生成 ticket」按钮：单选 → 四选一（epic/story/task/bug），
  按类型动态显示父级选择（story 必选 epic；task/bug 必选 epic+story）；
- 点击后 202 接收 → 轮询 ticket_request 状态（pending→done/failed）或
  经 webhook 推送刷新；成功后跳转/高亮新实体；
- 生成类型为 epic 时提示「该提案将落为 Epic，如需 Story 请在 Epic 下另建」。

## 5. 风险与边界

1. **异步链路依赖 agent 环境就绪**：worker 机器须装对应 agent CLI 且已配置
   AgentBoard MCP —— 与 Epic 96 澄清轮次前提一致，属既有约束非新增；
2. **双写一致性**：agent 经 MCP 创建实体时，若实体创建成功但回填失败
   （网络断），会产生孤儿实体 → 靠幂等键 + 启动时对账兜底；
3. **状态机迁移兼容**：存量 `story_created` 数据的读路径不能断
   （前端 proposal 列表、MCP proposal_get）；
4. **事件风暴**：ticket_created 若被 workflow_worker 再接评审/开发，
   注意与 Epic 122 既有事件（story.created 等）的触发关系，避免重复指派。

## 6. 落地建议（OpenSpec 切片）

- **切片 1（后端核心）**：状态机迁移（ticket_created + ticket_type/id）、
  ticket_request 表、`proposal.ticket_requested/created` 事件、worker 消费逻辑、
  MCP 工具 `proposal_create_ticket`、幂等与失败兜底；
- **切片 2（前端）**：四选一按钮 + 父级选择 + 状态轮询展示；
- **切片 3（对账与运营）**：孤儿实体对账、重试/人工兜底通道、事件接力接线
  （ticket_created → 评审/开发流转）。

每个切片遵循 OpenSpec：`proposal.md → design.md → tasks.md`，含 Alembic 迁移
与单测（沿用 InMemoryBroker + CAS 模式）；不触碰端口 18001。
