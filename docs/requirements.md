# AgentBoard 需求分析文档

> 版本：v0.1（草案）
> 方法：规格驱动（OpenSpec / Superpowers 风格），后续开发以 `docs/tasks.md` 为工作清单，每个变更通过 spec + tasks 推进。
> 状态：待评审

---

## 1. 项目背景与目标

需要一个**轻量版项目管理工具**，并内嵌 **OpenSpec / Superpowers 类的"规范驱动开发"能力**。

传统 OpenSpec / Superpowers 把 `spec.md` / `plan.md` 放在代码仓库文件里；本项目希望把这些 **markdown 内容直接挂在任务（task）上**，并通过 **MCP** 暴露给 AI 编程工具，让 Agent 能直接读写任务的 `description` 与 `spec`，从而把"写规范 → 生成任务 → 执行 → 更新状态"的闭环留在项目管理工具内。

本期目标（MVP）：
- 层级化项目管理：`Project → Epic → Story → Task/Bug`
- 任务携带 `description` 与 `spec`（均为 markdown）
- 提供 MCP 接口，供 AI Agent 查询 / 创建 / 更新
- 存储支持双后端：**调试用 SQLite，生产用 MariaDB**

---

## 2. 范围

**包含（本期）**
- 项目树 CRUD 与层级关系维护
- Task / Bug 两类工作项，含 `description`、`spec`、状态、类型
- 基于任务的过滤 / 搜索
- MCP 工具集（CRUD + spec 读写 + 状态流转）
- OpenSpec 风格的 spec/plan 模板：在 task 上挂载规范文档
- 双存储后端切换（SQLite / MariaDB）

**暂不纳入（后续）**
- 多用户 / 复杂权限与鉴权（MVP 单用户 / Agent 直接操作）
- 完整 Web UI（先提供数据层 + MCP，Web UI 作为可选阶段）
- 评论、附件、通知、看板拖拽等协作功能

---

## 3. 用户与角色（简化）

| 角色 | 说明 | 能力 |
|------|------|------|
| 人类用户 | 项目所有者 / 开发者 | 通过 CLI / Web / MCP 管理 |
| AI Agent | 编程助手（如 CodeBuddy） | 通过 MCP 读写任务与 spec，驱动开发闭环 |

MVP 不做权限区分，所有调用方等价。

---

## 4. 功能需求（FR）

**FR-1 项目树管理**
- `create_project / get_project / list_projects / update_project / delete_project`
- 项目下可建 `Epic`，Epic 下建 `Story`，Story 下建 `Task` / `Bug`

**FR-2 工作项（Task / Bug）**
- 字段：`id, project_id, parent_id, type(task|bug), title, status, description(md), spec(md), created_at, updated_at`
- `type` 区分任务与缺陷；`status` 走状态机（见 FR-5）

**FR-3 描述与规范（核心）**
- `description`：人类可读的任务描述（markdown）
- `spec`：OpenSpec / Superpowers 风格规范文档（markdown），例如变更提案 / 执行计划 / 验收标准
- 提供 `get_task_spec` / `set_task_spec` / `append_task_spec` 等 MCP 工具

**FR-4 查询与过滤**
- 按项目 / 史诗 / 故事过滤
- 按 `type`、`status` 过滤
- 关键字搜索 `title` / `description` / `spec`

**FR-5 状态流转**
- 状态建议：`backlog → todo → in_progress → in_review → done`（Bug 额外 `verifying`）
- 状态变更通过显式 MCP 工具，校验合法迁移

**FR-6 MCP 能力（面向 AI Agent）**
- 项目树 CRUD 工具
- `spec` 读写与模板生成工具（生成 OpenSpec 风格 change proposal）
- 搜索 / 过滤工具
- 状态流转工具
- 工具返回结构化数据（JSON），便于 Agent 解析

**FR-7 OpenSpec / Superpowers 类工作流（特色）**
- 在 task 上直接挂载 `spec`：可写入需求分析、设计、执行计划、验收标准
- 提供"生成变更提案"模板，自动填充到 task 的 `spec`
- 支持 `spec` 与子任务的双向关联（可选：从 spec 解析出子任务）

---

## 5. 非功能需求（NFR）

- **NFR-1 存储可切换**：通过环境变量（如 `AGENTBOARD_DB_URL`）在 SQLite 与 MariaDB 间切换；代码不感知具体数据库。
- **NFR-2 MCP 兼容**：遵循 Model Context Protocol，可被主流客户端（Claude Desktop / CodeBuddy 等）接入。
- **NFR-3 可部署性**：本地一条命令启动（SQLite）；生产环境提供 MariaDB 连接配置。
- **NFR-4 数据一致性**：层级删除采用软删除或级联策略明确化。
- **NFR-5 可测试性**：存储层与服务层具备单元测试，便于调试。

---

## 6. 数据模型（ER 概要）

```
Project (1) ──< (N) Epic
Epic    (1) ──< (N) Story
Story   (1) ──< (N) Task
Task    (N) ──< (N) Task   // 可选：task 可再拆子 task
```

| 实体 | 关键字段 |
|------|----------|
| Project | id, name, key(短码), description(md), created_at |
| Epic | id, project_id, title, description(md), status |
| Story | id, epic_id, title, description(md), status |
| Task | id, project_id, parent_id(nullable), story_id(nullable), type(task\|bug), title, status, description(md), spec(md), created_at, updated_at |

> 说明：`Task.parent_id` 支持嵌套；`story_id` 记录其归属 Story，便于从 Story 维度聚合。

---

## 7. 技术选型（建议，待确认）

| 维度 | 建议 | 理由 |
|------|------|------|
| 语言 | Python 3.11+ | MCP 生态成熟（FastMCP），与 SQLAlchemy 配合简单 |
| MCP 框架 | FastMCP（model-context-protocol） | 声明式工具定义，开发快 |
| ORM | SQLAlchemy 2.0 | 同时支持 SQLite 与 MariaDB，切换仅改 URL |
| 迁移 | Alembic | 与 SQLAlchemy 原生配合 |
| 驱动 | `pysqlite3`/`sqlite3`（调试）、`PyMySQL`（生产） | 双后端兼容 |
| 结构 | `storage` / `service` / `mcp` / `models` 分层 | 清晰、可测 |

> 备选：若团队偏好 TypeScript，可用 `TypeScript + FastMCP(ts) + Prisma/Drizzle`。**默认为 Python，待确认。**

---

## 8. 开放问题（需确认）

1. 技术栈是否采用 Python（默认）还是 TypeScript？
2. 是否需要 Web UI？还是本期仅 MCP + 数据层？
3. Task 是否允许无限嵌套，还是固定四级（Project/Epic/Story/Task）？
4. `spec` 模板是否遵循某固定 OpenSpec 结构（`proposal.md` / `tasks.md` / `design.md`），还是自由 markdown？
5. 生产 MariaDB 的连接信息 / 部署形态（容器？云？）？
6. 是否需要任务间的依赖 / 阻塞关系？

---

## 9. 术语表

- **Epic / Story / Task**：自上而下的工作分解层级。
- **Spec**：任务上的规范文档（markdown），承载 OpenSpec/Superpowers 风格内容。
- **MCP**：Model Context Protocol，AI 工具与本地服务通信协议。
- **OpenSpec / Superpowers**：以 markdown 规范驱动开发的 methodologies，本项目借鉴其结构。
