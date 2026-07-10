# AgentBoard 需求分析文档

> 版本：v0.2
> 方法：规格驱动（OpenSpec / Superpowers 风格），后续开发以 `docs/tasks.md` 为工作清单，每个变更通过 `openspec/changes/<id>/`（proposal + design + tasks）推进。
> 状态：基线已实现（数据层 / 服务层 / REST API / Web SPA / MCP 服务 / 后端鉴权接口）；新增 4 项能力待实现（Epic 7–10），另设**长期前端优化轨道**（FR-12 / Epic 11）。详见 `docs/tasks.md` 与 `openspec/changes/`。

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

**包含（本期 / 已基线）**
- 项目树 CRUD 与层级关系维护
- Task / Bug 两类工作项，含 `description`、`spec`、状态、类型
- 基于任务的过滤 / 搜索
- MCP 工具集（CRUD + spec 读写 + 状态流转）
- OpenSpec 风格的 spec/plan 模板：在 task 上挂载规范文档
- 双存储后端切换（SQLite / MariaDB）
- 后端鉴权接口：`/api/auth/register`、`/api/auth/login`、`/api/auth/me`（模型 + 加密 + service + 端点 + `users` 表迁移）
- Web SPA：项目树浏览 + 增删改 + 状态流转 + spec 编辑（前端**鉴权 UI 待补**）

**进行中 / 待实现（见 Epic 7–11）**
- FR-8 前端注册 / 登录 UI 与 token 持久化（后端已就绪）
- FR-9 MariaDB 独立 `.sql` 脚本 + 真实集成验证 + docker-compose 编排
- FR-10 前端 Web 自动化测试（Playwright 真实浏览器 E2E）
- FR-11 MCP 鉴权集成 + 运维化（启动脚本 / 客户端配置 / 冒烟测试）
- FR-12 持续前端优化（模仿 Jira，小步迭代）—— 长期轨道，详见 Epic 11

**暂不纳入（后续）**
- 多租户 / 细粒度权限（RBAC）、第三方 OAuth
- 评论、附件、通知、看板拖拽等协作功能
- 现有项目树 CRUD 默认仍为单用户开放（与 MCP / Web 兼容）；是否强制鉴权见 FR-8 设计决策

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

## 7. 本期新增能力（待实现）

**FR-8 前端注册 / 登录（前端集成）**
- 后端鉴权接口已就绪（`/api/auth/register|login|me`）。本次补齐**前端 SPA 的登录/注册界面与 token 生命周期**管理。
- 登录后服务端返回无状态 Bearer Token；前端存于 `localStorage`，后续所有 `fetch` 自动携带 `Authorization: Bearer <token>`。
- 未登录访问应用时展示登录 / 注册界面；登录成功后进入应用；提供登出按钮并清除 token。
- 头部展示当前用户名。设计决策：现有项目树 CRUD 默认保持单用户开放（与 MCP 兼容），是否通过 `AGENTBOARD_REQUIRE_AUTH` 开关强制鉴权在变更 `auth` 中决定。

**FR-9 MariaDB 数据库脚本与集成**
- 在 Alembic 迁移之外，提供**独立、可审阅的 MariaDB `schema.sql` 脚本**（建库、建表、索引、字符集 `utf8mb4`、用户与授权），便于 DBA / 容器初始化与离线评审。
- 验证 Alembic 迁移在真实 MariaDB 11 下可 `upgrade head`、功能与 SQLite 一致。
- docker-compose `db` profile 一键起 MariaDB；`AGENTBOARD_DB_URL=mysql+pymysql://...` 切换；提供集成冒烟测试（可选，需可用实例）。

**FR-10 前端 Web 自动化测试（Playwright）**
- 引入 Playwright（Chromium），**真实浏览器**驱动 SPA，而非 `httpx` 模拟。
- 覆盖：登录 / 注册 UI 流（含错误分支）、项目树 CRUD UI 操作、状态流转交互、markdown 渲染。
- 与现有 `tests/test_web_flow.py` 互补：后者验证"接口等价行为"，前者验证"真实 UI 行为"。
- 可脚本化启动 API + Web 服务，由 Playwright 对 `http://localhost:8080` 操作。

**FR-11 MCP 鉴权集成与运维化（实现 MCP）**
- MCP 服务（`mcp_server.py`）已完整实现工具集。本次目标：使其**生产可用并连通鉴权**。
- 新增 MCP 用户管理工具：`auth_register` / `auth_login` / `auth_me`，供 AI Agent 创建与校验身份。
- `api` 后端可选透传 Bearer Token（从环境变量 / 配置读取），以便调用受保护端点。
- 提供启动脚本与客户端配置样例（Claude Desktop `mcp.json`、CodeBuddy 配置），以及 MCP 冒烟测试（FastMCP 客户端调用工具验证）。
- README 补充 MCP 运行与接入说明。

**FR-12 持续前端优化（模仿 Jira，小步迭代）**
- 长期演进轨道：**不做破坏性重构、不擅自改动后端契约 / 数据模型** 的前提下，持续打磨 SPA，向 Jira 的交互密度与视觉语言靠拢（状态色、看板、行内编辑、详情抽屉、快捷键等）。
- 迭代纪律：每个自动任务周期**只交付一个小而独立、可立即验证的优化**；范围红线为单文件改动为主、新增代码 < ~80 行、不引入新框架 / 构建链。
- 边界：以纯前端（HTML/CSS/JS）为主；确需后端字段的 Jira 式能力（标签、负责人、截止日期、拖拽排序、评论）记入 backlog「需后端」分组，单独评估，不混入小优化。
- 验收：每项优化都需本地起服务手测通过，且现有 playwright / httpx 测试不被破坏。详细 backlog 与规则见 `docs/tasks.md` Epic 11 与 `openspec/changes/frontend-continuous/`。

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

1. 技术栈是否采用 Python（默认）还是 TypeScript？ → **已定：Python 3.11+ / FastAPI / FastMCP。**
2. 是否需要 Web UI？ → **已定：是，前后端分离 SPA（已实现，鉴权 UI 待补）。**
3. Task 是否允许无限嵌套，还是固定四级（Project/Epic/Story/Task）？ → **已定：固定四级，Task 为最底层不嵌套。**
4. `spec` 模板是否遵循某固定 OpenSpec 结构？ → **已定：自由 markdown，提供 OpenSpec 风格提案模板。**
5. 生产 MariaDB 的连接信息 / 部署形态？ → **方案：docker-compose `db` profile + `AGENTBOARD_DB_URL` 切换；独立 `.sql` 脚本便于离线评审（见 FR-9）。**
6. 是否需要任务间的依赖 / 阻塞关系？ → 暂不做。
7. **[新] 现有项目树 CRUD 是否强制鉴权？** → 默认保持单用户开放（与 MCP 兼容）；是否经 `AGENTBOARD_REQUIRE_AUTH` 开关强制，由变更 `auth` 决定。
8. **[新] Playwright 是否纳入 CI？** 运行时依赖浏览器二进制，建议本地 / CI 均可执行 `playwright install chromium`。
9. **[新] MCP 调用受保护端点时 token 来源？** 由 `AGENTBOARD_MCP_TOKEN` 或客户端注入；见变更 `mcp-auth`。

---

## 9. 术语表

- **Epic / Story / Task**：自上而下的工作分解层级。
- **Spec**：任务上的规范文档（markdown），承载 OpenSpec/Superpowers 风格内容。
- **MCP**：Model Context Protocol，AI 工具与本地服务通信协议。
- **OpenSpec / Superpowers**：以 markdown 规范驱动开发的 methodologies，本项目借鉴其结构。
