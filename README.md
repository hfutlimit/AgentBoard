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
