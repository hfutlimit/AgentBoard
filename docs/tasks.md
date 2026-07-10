# AgentBoard 任务列表

> 工作清单（Superpowers / OpenSpec 风格）。每个 Epic 对应一个能力，Story 对应一个可交付切片，Task 为可执行单元。
> 约定：`[ ]` 未开始，`[x]` 已完成。状态参考 `docs/requirements.md` FR-5。
> 开发顺序建议自上而下；Epic 6 用于以本项目的规范来管理本项目自身。

---

## Epic 1：数据模型与存储层
> 目标：可切换的双后端（SQLite / MariaDB）存储，承载项目树。

### Story 1.1 定义数据模型
- [ ] Task：绘制 ER 模型并固化到 `docs/requirements.md` §6
- [ ] Task：实现 SQLAlchemy 模型（Project / Epic / Story / Task）
- [ ] Task：定义状态枚举与合法迁移规则

### Story 1.2 存储抽象与切换
- [ ] Task：基于 `AGENTBOARD_DB_URL` 实现数据库引擎工厂（SQLite / MariaDB）
- [ ] Task：编写连接池与健康检查
- [ ] Task：Alembic 初始化与首版迁移脚本
- [ ] Task：存储层单元测试（SQLite 下跑通，验证 MariaDB DDL 兼容）

---

## Epic 2：核心服务层（CRUD）
> 目标：业务服务封装，供 MCP 与未来 UI 复用。

### Story 2.1 项目树 CRUD
- [ ] Task：`create_project / get_project / list_projects / update_project / delete_project`
- [ ] Task：`create_epic / get_epic / list_epics`
- [ ] Task：`create_story / get_story / list_stories`
- [ ] Task：`create_task`（type=task|bug）/ `get_task` / `list_tasks`
- [ ] Task：级联删除策略（Project 删除时处理子节点）

### Story 2.2 描述与规范读写
- [ ] Task：`set_task_description` / `get_task_description`
- [ ] Task：`set_task_spec` / `get_task_spec` / `append_task_spec`
- [ ] Task：spec 内容校验（markdown 解析不报错即接受）

### Story 2.3 查询、过滤与搜索
- [ ] Task：按 project / epic / story / type / status 过滤
- [ ] Task：关键字搜索 title / description / spec
- [ ] Task：服务层单元测试

---

## Epic 3：MCP 服务
> 目标：暴露工具给 AI Agent，驱动开发闭环。

### Story 3.1 基础 CRUD 工具
- [ ] Task：FastMCP 服务骨架 + 启动入口
- [ ] Task：注册项目树 CRUD 工具
- [ ] Task：注册 task 描述 / spec 读写工具
- [ ] Task：注册搜索 / 过滤工具
- [ ] Task：注册状态流转工具（校验合法迁移）

### Story 3.2 工具契约与返回结构
- [ ] Task：统一 JSON 返回结构（含 id / 错误信息）
- [ ] Task：MCP 工具入参 schema 校验
- [ ] Task：本地以 MCP 客户端联调（SQLite）

---

## Epic 4：OpenSpec / Superpowers 类能力（特色）
> 目标：把规范驱动开发留在任务里。

### Story 4.1 Spec 模板
- [ ] Task：定义 change-proposal 模板（背景 / 目标 / 范围 / 任务清单 / 验收）
- [ ] Task：提供"生成变更提案"工具，写入 task.spec
- [ ] Task：定义执行计划（plan）模板

### Story 4.2 规范与任务联动（可选）
- [x] Task：从 spec 解析清单项（- [ ] 标题）并批量建同级子 task（generate_tasks_from_spec）
- [x] Task：task 与 spec 双向引用（子任务记录 source_spec_id，源 spec 回写链接）
- [ ] Task：状态联动（spec 进入 review 时关联 task 转 in_review）

---

## Epic 5（可选）：REST API + Web UI
> 目标：人类可用的可视化管理。

### Story 5.1 REST API
- [ ] Task：FastAPI 暴露核心 CRUD
- [ ] Task：与 MCP 共用同一 service 层

### Story 5.2 简易 Web UI
- [ ] Task：项目树浏览
- [ ] Task：任务详情 + markdown 渲染（description / spec）
- [ ] Task：状态切换交互

---

## Epic 6：以本项目规范管理本项目
> 用 OpenSpec 结构沉淀 AgentBoard 自身的规格与变更。

### Story 6.1 规范目录
- [x] Task：建立 `openspec/specs/agentboard/spec.md`（能力规格 = 当前事实来源）
- [x] Task：建立 `openspec/changes/` 目录 + `openspec/AGENTS.md`（Agent 指引）
- [x] Task：将本 `docs/tasks.md` 与 OpenSpec `tasks.md` 对齐（后续变更走 `openspec/changes/*/tasks.md`）

### Story 6.2 首个变更提案
- [x] Task：以 OpenSpec 提案形式描述后续变更（见 `openspec/changes/mariadb-alembic/`）
- [ ] Task：MariaDB 接入 + Alembic 迁移 + MCP 工具补全（进行中，见 change 的 tasks.md）

---

## 验收总览
- [ ] SQLite 下完整跑通 CRUD + spec 读写 + 搜索
- [ ] MCP 客户端可连接并操作
- [ ] 切换 `AGENTBOARD_DB_URL` 到 MariaDB 后迁移可应用、功能一致
- [ ] `docs/requirements.md` §8 开放问题已确认或记录决策

---

## 开发进度（首次实现 · 2026-07-10）

本轮完成 MVP 骨架并通过 smoke test：

- [x] **Epic 1** 数据层：模型 + SQLite/MariaDB 切换（`create_all` 初始化，Alembic 留待后续）
- [x] **Epic 2** 服务层：项目树 CRUD、级联删除、spec 读写、搜索、状态机校验
- [x] **Epic 3** MCP：FastMCP 工具集（CRUD / spec / 搜索 / 状态 / `spec_proposal`）
- [x] **Epic 4（基础）** spec 模板：`spec_proposal` 生成 OpenSpec 风格提案；Web UI 可编辑 spec
- [x] **Epic 5** Web UI：项目树浏览 + 任务详情 + markdown 渲染 + 状态切换（服务端渲染，无独立 SPA）

### 重构（前后端分离 · 2026-07-10）
- [x] API / Web / MCP 三端拆分为独立服务
- [x] `api.py` 改为纯 REST/JSON + CORS（不再服务端渲染）
- [x] `web_app.py` + `web/static/` 独立 SPA，fetch 调 API
- [x] MCP 支持 `api`（httpx 调 API）/ `db`（直连）双后端
- [x] Web UI 支持对 project/epic/story/task/bug 全部增删改 + 状态流转 + spec 编辑
- [x] smoke test 覆盖四端

待办：
- [ ] Epic 4.2：从 spec 解析子任务 / 双向引用（可选）
- [ ] Epic 6：以 OpenSpec 目录（`openspec/`）管理本项目自身规格
- [x] Epic 7：前端注册 / 登录 UI（后端鉴权已就绪，仅前端集成）
- [x] Epic 8：MariaDB 独立 `.sql` 脚本 + 真实集成验证（Alembic 迁移已完成）
- [x] Epic 9：前端 Web 自动化测试（Playwright 真实浏览器 E2E）
- [x] Epic 10：MCP 鉴权集成 + 运维化（"实现 MCP"）

---

## Epic 7：前端注册 / 登录（鉴权 UI）
> 目标：补齐 SPA 的登录 / 注册界面与 token 生命周期。后端接口已就绪（见 `openspec/changes/auth/`）。

### Story 7.1 前端鉴权骨架
- [ ] Task：`app.js` 增加 `getToken/setToken/clearToken`（localStorage）
- [ ] Task：改造 `api()` 自动注入 `Authorization`；收到 401 清 token 回登录
- [ ] Task：`index.html` 预留登录 / 注册容器

### Story 7.2 登录 / 注册界面
- [ ] Task：`renderAuth()`：用户名 / 密码 + 登录/注册切换，调用 `/api/auth/register|login`
- [ ] Task：成功存 token 进应用；失败（409/401）展示错误
- [ ] Task：启动守卫：有 token 且 `/api/auth/me` 通过则进应用，否则显示登录

### Story 7.3 应用内用户态
- [ ] Task：顶部栏显示当前用户名 + 登出按钮
- [ ] Task：`style.css` 补充登录卡片 / 用户信息条样式
- [ ] Task（可选）：`api.py` 增加 `AGENTBOARD_REQUIRE_AUTH` 强制 CRUD 鉴权

---

## Epic 8：MariaDB 数据库脚本与集成
> 目标：独立 `.sql` 脚本 + 真实集成验证。Alembic 迁移与 MCP 工具已就绪（见 `openspec/changes/mariadb-alembic/`）。

### Story 8.1 独立 MariaDB 脚本
- [ ] Task：新增 `scripts/mariadb/schema.sql`（建库 utf8mb4、建用户授权、五表与 `models.py` 对齐、含 `source_spec_id`）
- [ ] Task：`scripts/mariadb/README.md` 说明初始化与离线评审用法

### Story 8.2 真实集成验证
- [ ] Task：用户提供 MariaDB 连接信息（`AGENTBOARD_DB_URL=mysql+pymysql://...`）
- [ ] Task：验证 Alembic `upgrade head` 在 MariaDB 11 建表 DDL 兼容
- [ ] Task：MariaDB 下跑通 service 层冒烟（CRUD + 状态机 + 搜索 + 生成子任务）
- [ ] Task：更新 `docker-compose.yml` 的 `db` profile 与 API 对接示例

### Story 8.3 集成测试
- [ ] Task：新增 `tests/test_mariadb_integration.py`（`skipif` 无 `AGENTBOARD_TEST_MARIADB`）

---

## Epic 9：前端 Web 自动化测试（Playwright）
> 目标：真实浏览器驱动 SPA 的 E2E。与 `test_web_flow.py` 互补（见 `openspec/changes/playwright-e2e/`）。

### Story 9.1 测试骨架
- [ ] Task：`requirements.txt` 增加 `playwright` / `pytest-playwright`
- [ ] Task：新增 `tests/test_playwright_e2e.py`：fixture 启动真实 API + Web（临时 SQLite）
- [ ] Task：UI 辅助函数 `ui_register / ui_login`

### Story 9.2 真实交互用例
- [ ] Task：注册 UI 流（进入应用 + localStorage 含 token）
- [ ] Task：登录 UI 流 + 错误密码 / 重复注册报错
- [ ] Task：项目树 CRUD UI（Project→Epic→Story→Task/Bug）
- [ ] Task：状态流转 UI（徽标更新）
- [ ] Task：spec 编辑与 markdown 渲染
- [ ] Task：README 补充 `playwright install chromium` 与运行命令

---

## Epic 10：MCP 鉴权集成与运维化（实现 MCP）
> 目标：使现成 MCP 服务连通鉴权并生产可用（见 `openspec/changes/mcp-auth/`）。

### Story 10.1 MCP 用户管理工具
- [ ] Task：`mcp_server.py` 新增 `auth_register` / `auth_login` / `auth_me`（api + db 双后端）

### Story 10.2 Token 透传与运维
- [ ] Task：`api` 后端 `_http` 支持 `AGENTBOARD_MCP_TOKEN` 注入 `Authorization`
- [ ] Task：启动脚本 `scripts/run_mcp.py` 或 README 写清 `python -m agentboard.mcp_server`
- [ ] Task：客户端配置样例 `examples/claude_desktop_mcp.json` / `examples/codebuddy_mcp.json`

### Story 10.3 验证与文档
- [ ] Task：新增 `tests/test_mcp_smoke.py`（FastMCP 客户端调用工具）
- [ ] Task：README「MCP 运行与接入」章节
- [ ] Task：更新 `openspec/specs/agentboard/spec.md` 的 MCP 工具清单
