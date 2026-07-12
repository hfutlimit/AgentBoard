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

# 3) 本地 MCP 服务（stdio）
#    默认调用 API：需先启动上面的 API
python -m agentboard.mcp_server
#    或让 MCP 直连数据库（无需 API）：
#    $env:AGENTBOARD_MCP_BACKEND="db"; python -m agentboard.mcp_server

# 4) 远程 MCP（Streamable HTTP，默认要求 Bearer Token）
#    API 与 MCP 必须使用相同的 AGENTBOARD_SECRET
$env:AGENTBOARD_SECRET="replace-with-at-least-32-random-bytes"
$env:AGENTBOARD_MCP_TRANSPORT="http"
$env:AGENTBOARD_MCP_HOST="0.0.0.0"
$env:AGENTBOARD_MCP_PORT="8001"
python -m agentboard.mcp_server
# MCP endpoint: http://127.0.0.1:8001/mcp
```

配置项（环境变量）：
- `AGENTBOARD_DB_URL`：数据库地址。默认 `sqlite:///./agentboard.db`；生产 `mysql+pymysql://user:pass@host:3306/agentboard`
- `AGENTBOARD_API_URL`：Web/MCP 调用的 API 地址，默认 `http://127.0.0.1:8000`
- `AGENTBOARD_MCP_BACKEND`：`api`（默认）或 `db`
- `AGENTBOARD_MCP_TRANSPORT`：`stdio`（默认）或 `http`（Streamable HTTP）
- `AGENTBOARD_MCP_HOST` / `AGENTBOARD_MCP_PORT` / `AGENTBOARD_MCP_PATH`：远程 MCP 监听配置，默认 `127.0.0.1:8001/mcp`
- `AGENTBOARD_MCP_REQUIRE_AUTH`：远程 MCP Bearer 鉴权，默认开启；只应在本机调试时关闭
- `AGENTBOARD_MCP_TOKEN`：stdio + API 后端调用受保护 REST 时使用的登录 Token
- `AGENTBOARD_REQUIRE_AUTH`：设为 `1` 时统一保护 REST 业务端点
- `AGENTBOARD_ALLOW_REGISTRATION`：设为 `0` 时仅允许创建首个用户，之后注册返回 403；当前 Docker 配置为方便测试保持开启，生产应设为 `0`
- `AGENTBOARD_TOKEN_TTL_SECONDS`：Token 有效期，默认 2592000 秒（30 天）
- `AGENTBOARD_SECRET`：登录 Token 签名密钥（HMAC）。默认内置不安全占位值，**生产务必设置**。
- `AGENTBOARD_ENV`：环境标识；设为 `production` 时强制检查 REST 鉴权、强密钥和 CORS 白名单。
- `AGENTBOARD_CORS_ORIGINS`：逗号分隔的 Web 来源白名单；本地默认 `*`，生产必须显式配置。

## 鉴权（注册 / 登录）

内置轻量鉴权（无额外依赖；新注册密码至少 8 位，密码使用可升级轮次的 PBKDF2 哈希，Token 为带过期时间的 HMAC 签名无状态 Bearer）：

- `POST /api/auth/register`：`{"username","password"}` → `201` 返回 `{id,username,token}`；重复用户名 → `409`
- `POST /api/auth/login`：`{"username","password"}` → `200` 返回 `{id,username,token}`；凭据错误 → `401`
- `GET /api/auth/me`：带 `Authorization: Bearer <token>` → `200` 返回当前用户；缺失/伪造 → `401`

> 本地默认保持 CRUD 开放；远程部署设置 `AGENTBOARD_REQUIRE_AUTH=1`。注册、登录和 `/api/meta` 保持公开。远程 MCP 默认始终要求同一枚 Bearer Token。

## 远程部署与 Agent 接入

### Docker Compose

先生成并设置强随机密钥，再启动 API、Web 和 MCP：

```powershell
Copy-Item .env.example .env
# 编辑 .env，把 AGENTBOARD_SECRET 换成：
python -c "import secrets; print(secrets.token_hex(32))"
docker compose up -d --build
```

默认端口：API `8000`、MCP `8001/mcp`、Web `8080`。生产环境应由 Nginx/Caddy/云网关终止 TLS，只向 Agent 暴露 `https://.../mcp`；不要在公网使用明文 HTTP，也不要直接暴露数据库。Nginx 样例见 [examples/nginx-agentboard.conf](examples/nginx-agentboard.conf)，其中已关闭 MCP 响应缓冲并转发 Authorization。

### 获取 Agent Token

首次注册（Docker 默认只允许创建首个用户，之后自动拒绝公开注册）：

```bash
curl -X POST https://agentboard.example.com/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"codex-agent","password":"replace-with-strong-password"}'
```

以后登录续签：

```bash
curl -X POST https://agentboard.example.com/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"codex-agent","password":"replace-with-strong-password"}'
```

响应中的 `token` 同时用于 REST 和 MCP。需要创建更多 Agent 账号时，可在受控维护窗口临时设置 `AGENTBOARD_ALLOW_REGISTRATION=1`，创建完成后立即恢复为 `0`。

### Codex

```powershell
$env:AGENTBOARD_TOKEN="v1.REPLACE_WITH_LOGIN_TOKEN"
codex mcp add agentboard `
  --url https://mcp.agentboard.example.com/mcp `
  --bearer-token-env-var AGENTBOARD_TOKEN
codex mcp get agentboard
```

重启或新建 Agent 会话后，确认可以看到 `list_projects`、`list_epics`、`list_stories`、`list_tasks` 等工具。

### 其他 MCP Agent

支持 Streamable HTTP 和自定义请求头的 Agent 使用：

```json
{
  "mcpServers": {
    "agentboard": {
      "url": "https://mcp.agentboard.example.com/mcp",
      "headers": {
        "Authorization": "Bearer v1.REPLACE_WITH_LOGIN_TOKEN"
      }
    }
  }
}
```

可复制 [examples/mcp-remote.json](examples/mcp-remote.json)；本地 stdio 示例见 [examples/mcp-stdio.json](examples/mcp-stdio.json)。客户端字段名可能不同，但连接要素始终是 Streamable HTTP URL 与 Bearer Token。

## 测试

- `tests/test_smoke.py`：四端冒烟（service / REST / Web / MCP）。
- `tests/test_backend_flow.py`：**后端自动化测试**，真实启动 uvicorn 子进程，针对已运行的 API 做 HTTP 端到端验证：注册/登录/错误分支 + 全链路 CRUD（project → epic → story → task/bug）与状态机校验。
- `tests/test_web_flow.py`：**Web 端到端自动化测试**，同时启动真实 API 与真实 Web 服务，校验 SPA 被正确托管并接到运行中的 API，并覆盖注册/登录、各类 ticket 的创建/修改、以及项目/epic/story/task 列表与搜索读取。
- `tests/test_mcp_smoke.py`：启动真实 Streamable HTTP MCP，验证无 Token 拒绝、Bearer 登录、工具发现和 Project → Epic → Story → Task 完整链路。
- `tests/test_playwright_e2e.py`：**前端 E2E 真实浏览器测试**（FR-10 / Epic 9）。用真实 Chromium 驱动 SPA，验证注册 / 登录 UI 流与 DOM 行为（与 `test_web_flow.py` 的 httpx 等价校验互补）。覆盖按 Epic 9 切片推进：Story 9.1 为测试骨架（`servers` fixture + `ui_register` / `ui_login` 辅助 + 注册/登录冒烟）；Story 9.2 的真实交互用例（CRUD UI / 状态流转 / spec 编辑 / 错误分支）后续切片。

```bash
PYTHONPATH=. python -m pytest tests/ -q

# 仅跑前端 E2E（首次需安装浏览器二进制）：
pip install playwright && playwright install chromium
PYTHONPATH=. python -m pytest tests/test_playwright_e2e.py -q
```

## 数据库迁移（Alembic）

`init_db()` 执行 `alembic upgrade head`。迁移失败时服务会中止启动，不再用 `create_all` 静默掩盖结构或权限错误。

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
