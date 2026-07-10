# AgentBoard

轻量项目管理工具，内嵌 **OpenSpec / Superpowers 风格的规范能力**：任务的 `spec` 字段存放 markdown 规范文档，并通过 **MCP** 暴露给 AI 编程工具。

## 功能

- 层级结构：`Project → Epic → Story → Task/Bug`（Task 为最底层，不嵌套）
- Task 携带 `description`(markdown) 与 `spec`(markdown)
- MCP 服务：项目树 CRUD、spec 读写、关键字搜索、状态流转、生成变更提案
- 简易 Web UI（FastAPI 服务端渲染，markdown 渲染）
- 双存储：调试用 SQLite，生产用 MariaDB（通过 `AGENTBOARD_DB_URL` 切换，代码不感知具体库）

## 架构（前后端分离）

三端相互独立，共享 `service` + `database` 层：

```
[Web SPA]  --fetch-->  [REST API]  --->  [service/DB]
[MCP]      --httpx-->  [REST API]        （或 MCP 直连 DB）
```

- **API**（`agentboard/api.py`）：纯 JSON REST，带 CORS，不含任何 HTML。
- **Web**（`agentboard/web_app.py` + `web/static/`）：独立 SPA，浏览器 fetch 调 API。
- **MCP**（`agentboard/mcp_server.py`）：默认 httpx 调 API，可切换直连 DB。

## 目录结构

```
agentboard/
  models.py       # SQLAlchemy 模型（Project/Epic/Story/Task）
  database.py     # 引擎工厂（SQLite/MariaDB 切换）+ session
  service.py      # 业务服务层（CRUD / spec / 搜索 / 状态机）
  api.py          # REST API（纯 JSON，前后端分离的后端）
  web_app.py      # Web 前端托管（独立服务）
  web/static/     # SPA：index.html / app.js / style.css
  mcp_server.py   # FastMCP 工具集（api / db 双后端）
tests/test_smoke.py
docs/requirements.md   # 需求分析
docs/tasks.md          # 任务列表（Epic/Story/Task）
```

## 运行

```bash
pip install -r requirements.txt

# 1) 启动 REST API（默认 SQLite，端口 8000）
uvicorn agentboard.api:app --reload --port 8000

# 2) 启动 Web 前端（独立服务，端口 8080）
uvicorn agentboard.web_app:app --reload --port 8080
# 浏览器打开 http://127.0.0.1:8080

# 3) MCP 服务（stdio）
#    默认调用 API：需先启动上面的 API
python -m agentboard.mcp_server
#    或让 MCP 直连数据库（无需 API）：
#    $env:AGENTBOARD_MCP_BACKEND="db"; python -m agentboard.mcp_server
```

配置项（环境变量）：
- `AGENTBOARD_DB_URL`：数据库地址。默认 `sqlite:///./agentboard.db`；生产 `mysql+pymysql://user:pass@host:3306/agentboard`
- `AGENTBOARD_API_URL`：Web/MCP 调用的 API 地址，默认 `http://127.0.0.1:8000`
- `AGENTBOARD_MCP_BACKEND`：`api`（默认）或 `db`
- `AGENTBOARD_SECRET`：登录 Token 签名密钥（HMAC）。默认内置不安全占位值，**生产务必设置**。

## 鉴权（注册 / 登录）

内置轻量鉴权（无额外依赖；密码 pbkdf2 哈希，Token 为 HMAC 签名无状态 Bearer）：

- `POST /api/auth/register`：`{"username","password"}` → `201` 返回 `{id,username,token}`；重复用户名 → `409`
- `POST /api/auth/login`：`{"username","password"}` → `200` 返回 `{id,username,token}`；凭据错误 → `401`
- `GET /api/auth/me`：带 `Authorization: Bearer <token>` → `200` 返回当前用户；缺失/伪造 → `401`

> 现有项目树 CRUD 接口保持单用户开放（与 MCP / Web 兼容）；鉴权接口用于身份创建与校验。

## 测试

- `tests/test_smoke.py`：四端冒烟（service / REST / Web / MCP）。
- `tests/test_backend_flow.py`：**后端自动化测试**，真实启动 uvicorn 子进程，针对已运行的 API 做 HTTP 端到端验证：注册/登录/错误分支 + 全链路 CRUD（project → epic → story → task/bug）与状态机校验。
- `tests/test_web_flow.py`：**Web 端到端自动化测试**，同时启动真实 API 与真实 Web 服务，校验 SPA 被正确托管并接到运行中的 API，并覆盖注册/登录、各类 ticket 的创建/修改、以及项目/epic/story/task 列表与搜索读取。

```bash
PYTHONPATH=. python -m pytest tests/ -q
```

## 数据库迁移（Alembic）

`init_db()` 优先执行 `alembic upgrade head`（正式迁移）；若 Alembic 不可用则降级为 `create_all` + 轻量在线迁移（开发期兼容）。

```bash
alembic revision --autogenerate -m "描述"   # 生成迁移
alembic upgrade head                        # 应用迁移
```

> 注意：`alembic.ini` 为 ASCII，避免 Windows 下 GBK 读取报错；`env.py` 复用 `AGENTBOARD_DB_URL` 与项目 engine。

## 测试（smoke test）

```bash
PYTHONPATH=. python tests/test_smoke.py
```

## 需求与任务

- `docs/requirements.md`：需求分析
- `docs/tasks.md`：任务列表（Epic/Story/Task）
- `openspec/`：**OpenSpec 规范驱动开发**目录
  - `openspec/specs/agentboard/spec.md`：当前能力的唯一事实来源
  - `openspec/changes/<id>/`：变更提案（proposal / design / tasks）
  - `openspec/AGENTS.md`：AI Agent 使用指引

开发遵循 Superpowers / OpenSpec 规范：新功能先写变更提案，按 `tasks.md` 实现，完成后同步 `specs/`。
