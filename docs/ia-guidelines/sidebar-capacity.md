# AgentBoard Sidebar IA 容量守则

> 状态：Active
> 维护：前端架构组
> 适用范围：AgentBoard 全部 workspace sidebar 容量决策
> 最近更新：2026-08-20

## 1. 背景

AgentBoard 自 Epic 149 / 150 完成后，前端结构稳定为：

```
AppShell
└── ProjectWorkspaceShell
    ├── WorkspaceTopbar
    ├── WorkspaceSidebar  ← 本守则针对这一层
    └── <router-outlet>
```

`WorkspaceSidebar` 当前承载项目内 8 个一级导航 item。本守则目的是防止 sidebar 无限扩张、再次落入「功能列表型产品」陷阱——参考 GitHub / Linear / Jira / Notion 等成熟产品的长期 IA 演进路径。

## 2. 容量阈值

| sidebar item 数 | 处置 |
|---|---|
| **≤ 8** | 维持单层 sidebar |
| **= 9** | 启动评估：是否需新增分组？是否能合并到已有分组？必须在本 Story 启动时同时输出 ADR |
| **= 10** | **强制** 开分组折叠 Story；本项不可豁免 |
| **> 10** | 立即回退违规 item；Sprint Review 复盘；不允许"先加再看" |

**关键规则**：sidebar item 计数器在每次 PR 涉及 sidebar 时自动 +1，并在 PR 描述里 link 本守则。

## 3. 5 分组（命名 freeze）

下列 5 个分组是 AgentBoard sidebar 的固定分组，新功能必须**优先**塞入已有分组，不允许默认新增顶层 item。

| 分组 | 范围 | 当前 item | 未来可能加入 |
|---|---|---|---|
| **OVERVIEW** | 项目首页 / 仪表盘 | Overview | — |
| **PLAN** | 动词性功能（计划/组织/追踪/决策） | Kanban, Epics, WorkItems, Proposals | — |
| **KNOWLEDGE** | 知识资产 / 文档 / 学习 | Documents | Learning（独立 Story） |
| **AUTOMATION** | Agent / Run / 调度 / 自动化 | （空） | Agents, Runs, Schedules |
| **PROJECT** | 项目元信息 / 成员 / 配置 | Members, Settings | Webhooks |

**命名规范**：
- 动词性功能（Kanban/Epics/Workflow）入 PLAN
- 资源/资产（Documents/Learning）入 KNOWLEDGE
- Agent 相关（Agents/Runs/Reviews/Allocation）入 AUTOMATION
- 项目管理（Members/Settings/Webhooks）入 PROJECT

## 4. 新增 item 强制流程

任何在 `WorkspaceSidebar` 新增顶层 item 的 PR，必须按以下顺序执行：

```
1. 能否塞入已有 5 分组之一？
   ├─ 是 → 直接入组；更新本守则"当前 item"清单
   └─ 否 ↓

2. 是否需要新增分组？
   ├─ 否 → 不允许新增；评估后塞入已有分组或撤回
   └─ 是 ↓

3. 提交 ADR（含以下内容）：
   - 产品定位（这个分组解决什么问题）
   - 不入已有 5 分组的理由
   - 用户研究 / 数据支撑
   - 未来 6 个月预计的 item 数（证明分组可持续）
   - 在 PR 描述里 link ADR

4. sidebar item 计数器自增 +1
5. 更新本守则"分组清单"+"历史变更"
6. 提交 PR；ADR Review 通过才能合并
```

**默认动作 = 不允许新增**。任何想绕过的尝试都视为产品债累积。

## 5. 守则审计机制

### 5.1 自动审计（PR 阶段）

- PR 涉及 `frontend/src/app/workspace-sidebar/` 或 `workspace-sidebar.html` 时，CI 检查：
  - 当前 sidebar item 计数
  - 是否新增顶层 item
  - 如新增，是否 link ADR
- 未通过 → 阻塞 PR

### 5.2 Sprint Review 审计

每个 Sprint Review 由前端架构组对 sidebar 做一次审计：

| 计数 | 行动 |
|---|---|
| `< 10` | 维持；记录在 sprint review 报告 |
| `= 10` | 立即开分组折叠 Story（已写入 backlog：触发性 Story） |
| `> 10` | 回退违规 item + ADR 复盘 + 写入 lessons-learned |

### 5.3 触发性 Story

当 sidebar item 数达 10 时自动开 Story「Sidebar 5 分组折叠实现」：

- 抽取 `SidebarGroupComponent`（分组 + 折叠）
- 迁移当前 8 item 到对应分组
- 保留 item 顺序 + 折叠状态（localStorage 持久化）
- E2E：5 分组各自折叠/展开 + 当前 active item 可见
- 不破坏现有 WorkspaceSidebar 任何 E2E

## 6. 移动端兼容

sidebar item 同样适用于移动端 bottom navigation：

- 当前底部 5-6 个核心入口（Home / Projects / 当前项目 8 tab / Settings）
- 与 sidebar 互斥显示（CSS media query）
- 移动端 5 分组折叠同样适用（折叠态在 mobile 表现为下拉）

## 7. 历史变更

| 日期 | 事件 | sidebar item 数 |
|---|---|---|
| 2026-08-20 | 守则建立（Epic 152 / Story 331） | **8** |

## 8. 关联

- 上游：Epic 149 静态 Review「8 个 workspace 导航已经接近认知上限」
- 配套：Epic 152 / Story 333（路由化是分组折叠的前置）
- 触发：本守则达到 10 item 时，自动开「Sidebar 5 分组折叠实现」Story

## 9. 决策记录

### 9.1 2026-08-20 守则建立

**背景**：Epic 150 X3 落地后 sidebar 8 item 稳定，展望未来 6 个月预计加入 Agents / Runs / Reviews / Learning / Knowledge / Allocation 等功能，sidebar 将快速突破 10 item 上限。

**决策**：提前建立 5 分组 + 容量阈值守则，避免再次落入"功能列表型产品"。

**反对意见**：
- "8 item 还没到 10，急什么" → 答：提前建立规则比事后推翻简单 10 倍；第 9 个 item 出现时再立规则已经晚了（PR 已合并）
- "分组后折叠反而是噪音" → 答：8 item 分 5 组时确实会显得空，但分组本身是 IA 信号，告诉用户"这块是 PLAN、那块是 AUTOMATION"；折叠态用户可一键展开

**生效**：立即。
