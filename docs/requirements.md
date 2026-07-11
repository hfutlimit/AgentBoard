# AgentBoard 需求分析文档

> 版本：v0.3
> 方法：规格驱动（OpenSpec / Superpowers 风格），后续开发以 `docs/tasks.md` 为工作清单，每个变更通过 `openspec/changes/<id>/`（proposal + design + tasks）推进。
> 状态：基线已实现（数据层 / 服务层 / REST API / Web SPA / MCP 服务 / 后端鉴权接口）；v0.3 将产品定位收敛为“面向人和开发 Agent 的轻量 Jira”，新增优先级、评论、附件、Sprint 与定时 Agent 开发闭环。详见 `docs/tasks.md` 与 `openspec/changes/jira-agent-core/`。

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
- Jira 核心协作：任务优先级、评论、附件、Sprint 规划
- Agent 协作：Codex、WorkBuddy、Qoder 等通过 MCP 查询/领取任务、同步状态、追加评论与交付记录
- 自动开发：用户可为任务配置一次性或周期性计划，由 Agent 执行器按时领取并回写运行结果

**进行中 / 待实现（见 Epic 7–12）**
- FR-8 前端注册 / 登录 UI 与 token 持久化（后端已就绪）
- FR-9 MariaDB 独立 `.sql` 脚本 + 真实集成验证 + docker-compose 编排
- FR-10 前端 Web 自动化测试（Playwright 真实浏览器 E2E）
- FR-11 MCP 鉴权集成 + 运维化（启动脚本 / 客户端配置 / 冒烟测试）
- FR-12 持续前端优化（模仿 Jira，小步迭代）—— 长期轨道，详见 Epic 11
- FR-13/14 优先级与评论—— 首个纵向切片已实现
- FR-15/16 附件与 Sprint—— 已完成规格，待实现
- FR-17 Agent MCP 与定时开发闭环—— 已完成安全边界与任务拆分，待实现

**后续增强（不阻塞 Jira 核心闭环）**
- 多租户 / 细粒度权限（RBAC）、第三方 OAuth
- 通知、看板拖拽、工时、发布版本、复杂报表
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
- 支持 `spec` 与生成任务的双向关联：从 spec 清单解析出的工作项仍是同一 Story 下的**同级 Task**，通过 `source_spec_id` 关联来源；不引入 Task 嵌套层级。

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
- 覆盖持续前端优化的关键 DOM 行为：窄屏布局、Toast 并发堆叠、任务详情抽屉的打开/关闭/状态更新；HTTP 200、静态资源关键字检查和后端流程测试不能替代这些验收。
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
- 迭代纪律：每个自动任务周期**只交付一个小而独立、可立即验证的优化**；范围红线为单文件改动为主、一次交付在全部前端文件中的**新增代码合计** < ~80 行、不引入新框架 / 构建链。不能只统计 JS 或“逻辑行”来规避口径。
- 超过红线时必须在编码前拆成可独立验收的子项；若审查后才发现超限，保留完成事实，但记录为流程例外并补齐真实浏览器回归范围，不将例外解释为规则已满足。
- 边界：以纯前端（HTML/CSS/JS）为主；确需后端字段的 Jira 式能力（标签、负责人、截止日期、拖拽排序、评论）记入 backlog「需后端」分组，单独评估，不混入小优化。
- 验收：每项优化都需本地起服务手测通过，且现有 playwright / httpx 测试不被破坏。详细 backlog 与规则见 `docs/tasks.md` Epic 11 与 `openspec/changes/frontend-continuous/`。

**FR-13 优先级（Jira 核心）**
- Task / Bug 必须具有 `priority`：`highest | high | medium | low | lowest`，默认 `medium`。
- 创建、编辑、列表、搜索、MCP 工具均可读写/筛选优先级；Web 使用有辨识度但不喧宾夺主的优先级徽章。
- Agent 领取工作时默认先考虑 Sprint 内未完成且优先级更高的任务。

**FR-14 评论与活动记录（Jira 核心）**
- 每条评论包含 `task_id, author, content(markdown), created_at, updated_at`；支持添加、列表与删除。
- 人类与 Agent 使用同一评论流。Agent 在开始、阻塞、完成时可写入简短进展，避免把过程信息塞入 description。
- 评论按创建时间正序展示；删除任务时级联删除评论。

**FR-15 附件（Jira 核心）**
- 任务可上传、列出、下载、删除附件；元数据至少包含原文件名、MIME、大小、存储键、上传者与时间。
- 默认采用本地文件系统存储并通过配置切换根目录；限制单文件大小并阻止路径穿越，数据库只保存元数据。
- MCP 支持列出附件、登记/读取受控附件；二进制上传可由 REST 完成，MCP 返回可访问的资源信息。

**FR-16 Sprint（Jira 核心）**
- Project 下可创建 Sprint，字段至少包含 `name, goal, status(planned|active|closed), start_at, end_at`。
- Task 可归入一个 Sprint；支持 Backlog 与 Sprint 看板、启动/关闭 Sprint，同一项目最多一个 active Sprint。
- 关闭 Sprint 时，未完成任务可移回 Backlog 或迁移到目标 Sprint。

**FR-17 Agent MCP 与定时开发闭环**
- MCP 提供面向 Codex、WorkBuddy、Qoder 等客户端的稳定工具：查询待办、读取上下文、更新状态/优先级、评论进展、读取 Sprint 与附件。
- 任务可配置 `AgentSchedule`：`task_id, agent, schedule_type(once|cron), schedule_expr/run_at, enabled, next_run_at, last_run_at`。
- 调度器只负责生成可审计的 Agent Run/触发请求，不在 Web 进程中直接执行任意 shell；执行器通过明确的命令模板/适配器运行，并回写 `queued|running|succeeded|failed|cancelled`、摘要与日志引用。
- 每次运行必须具备幂等键和租约，避免多实例重复执行；默认不自动 push/merge，除非具体任务策略明确授权。

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
Project (1) ──< (N) Sprint ──< (N) Task
Task    (1) ──< (N) Comment / Attachment / AgentSchedule / AgentRun
```

| 实体 | 关键字段 |
|------|----------|
| Project | id, name, key(短码), description(md), created_at |
| Epic | id, project_id, title, description(md), status |
| Story | id, epic_id, title, description(md), status |
| Task | id, project_id, story_id(nullable), sprint_id(nullable), type(task\|bug), title, status, priority, description(md), spec(md), created_at, updated_at |
| Sprint | id, project_id, name, goal, status, start_at, end_at |
| Comment | id, task_id, author, content(md), created_at, updated_at |
| Attachment | id, task_id, filename, mime_type, size, storage_key, uploader, created_at |
| AgentSchedule | id, task_id, agent, schedule_type, schedule_expr/run_at, enabled, next_run_at, last_run_at |
| AgentRun | id, schedule_id, task_id, agent, status, idempotency_key, summary, log_ref, timestamps |

> 说明：Task 为最底层工作项、不再嵌套；`story_id` 记录其归属 Story，便于从 Story 维度聚合。

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
