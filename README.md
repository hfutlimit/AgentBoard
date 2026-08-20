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
- **Web**（`frontend/` + `agentboard/web_app.py`）：Angular 21 LTS 独立 SPA，构建后由 FastAPI 托管，浏览器通过 `HttpClient` 调 API。
- **MCP**（`agentboard/mcp_server.py`）：仅通过 httpx 调用 REST API，不直接访问数据库。

### 2026-08 起：双栈 BFF 过渡（Stage 0+）

公开入口从 FastAPI 单体演进为「.NET 10 BFF + FastAPI 内部 AI 服务」双栈。详细架构图、Feature 归属矩阵与数据访问边界见 [`docs/architecture-v2.md`](docs/architecture-v2.md)：

```mermaid
flowchart LR
    subgraph External["External Clients"]
        Web["Angular 21 SPA"]
        MCPClient["MCP Clients"]
        SDK["External SDKs"]
    end

    subgraph Edge["Reverse Proxy"]
        Nginx["nginx :80/:443<br/>+ TLS + X-Request-Id"]
    end

    subgraph BFF[".NET 10 BFF (S0+)"]
        DotNet["AgentBoard.Api :18000"]
        SignalR["SignalR /hubs/agents<br/>(Stage 2)"]
        DotNet -. hosts .-> SignalR
    end

    subgraph Legacy["FastAPI (Source of Truth)"]
        FastAPI["agentboard.api:app :8000"]
        McpServer["mcp_server.py<br/>(stdio + http)"]
        Alembic["Alembic<br/>(DB schema 真源)"]
    end

    Data[("MariaDB 10.11<br/>+ RabbitMQ 3.13")]

    Web --> Nginx
    MCPClient --> Nginx
    SDK --> Nginx
    Nginx -->|"/api/* (Stage 2+)<br/>weight 10..100"| DotNet
    Nginx -->|"/api/* (Stage 0/1)<br/>weight 90..0"| FastAPI
    Nginx -->|"/mcp/*"| McpServer
    DotNet -->|HttpClient<br/>+ traceparent| FastAPI
    DotNet -. read-only .-> Data
    FastAPI --> Data
    Alembic --> Data
    McpServer -->|httpx| FastAPI
```

**当前状态（Stage 0 完成）**：
- ✅ .NET 10 脚手架 + 健康/元数据双端点 1:1 兼容 FastAPI
- ✅ 契约冻结：FastAPI 仍是公开 REST 契约真源
- ✅ 双栈 docker-compose 一键启停（`scripts/dev-up.ps1`）
- ✅ Serilog + OpenTelemetry 接入（X-Request-Id + traceparent 跨栈）
- ⏳ 切流：Stage 2 灰度（nginx upstream 权重 10 → 100）
- ⏳ SignalR：Stage 2 全新
- ⏳ 写路径迁 .NET：Stage 2+

完整 Stage 0~3 任务清单：[`openspec/changes/dual-stack-bff-restructure/tasks.md`](openspec/changes/dual-stack-bff-restructure/tasks.md)。
运维 & 切流手册：[`docs/dual-stack-bff-runbook.md`](docs/dual-stack-bff-runbook.md)。
.NET 端规约：[`dotnet/README.md`](dotnet/README.md)。

## 目录结构

> 2026-08 垂直切片重构后（`docs/refactor-plan.md` 9 阶段落地）：所有业务逻辑按
> feature 模块拆分,顶层文件保持薄 facade 兼容老 import。

```
agentboard/
  api.py                # 薄 facade: lifespan + middleware + app.include_router(...)
  api_helpers.py        # 共享 helper: _current_user / _auth_is_required / _ser / ...
  schemas.py            # 58 个 Pydantic BaseModel 集中地(router 与 api.py 共享)
  service.py            # 业务 facade(末尾 re-bind features/*/service 的公共 API)
  models.py             # SQLAlchemy 模型(facade,实际定义见 domains/* + features/*)
  database.py           # 引擎工厂 + session(facade,见 core/infrastructure/database)
  auth.py / cache.py / cos_client.py  # 同样为 facade,实现见 core/infrastructure
  mq.py                 # RabbitMQ 事件总线(Event/Consumer/Producer)
  executor.py / scheduler.py / workflow_worker.py  # 异步执行链路
  mcp_server.py         # MCP 入口,工具函数分发到 features/mcp/<feature>.py
  web_app.py            # Web 前端托管
  web/static/           # Angular 复用的全局设计系统 style.css
  core/                 # 跨 feature 共享底座
    config.py           # 配置(settings / 路径 / 常量)
    exceptions.py       # DomainError / NotFound / Duplicate / InvalidValue / IllegalTransition
    state_machine.py    # 通用状态机基类(StateMachine[T])
    service_helpers.py  # _commit / _paginate / _required / _check_* / ...
    observability/      # logging / metrics / tracing
    infrastructure/     # database / auth / cache / cos_client(实际实现位置)
    api/                # app / deps / middleware / errors
  domains/              # 跨 feature 共享的领域原语
    common/enums.py     # ItemType / Status / Priority / ...
    identity/models.py  # User / ApiKey
    projects/models.py  # Project / Epic / Story / Sprint / ProjectMember / ...
    work_items/models.py    # Task / Comment / Attachment / TaskDependency / WebhookConfig
    documents/models.py # Document / DocumentFolder / DocumentComment / DocumentRevision
    proposals/models.py # Proposal / ProposalRound / ProposalQuestion / ProposalTicketRequest
    scheduling/models.py    # Agent / AgentSchedule / AgentRun
  features/             # 业务领域,按 feature 垂直切片
    identity/           # 用户/密码/API Key/Auth service
    projects/           # Project/Epic/Story/Sprint/Member service + router
    work_items/         # Task/Comment/Attachment/状态机 + router
    proposals/          # 需求澄清提案流 + 状态机 + ticket 转换 + router
    documents/          # 文档/版本/评论/文件夹 + router
    scheduling/         # Agent/Schedule/Run + Review 流程 + router
    notifications/      # 站内通知 + router
    webhooks/           # 事件 webhook 派发 + router
    admin/              # meta/health/audit/admin 端点 + router
    search/             # 全文搜索端点 + router
    auth/               # 登录/注册/me/api-keys router(走 identity service)
    mcp/                # MCP 工具的 HTTP 客户端 helper(按 feature 拆分)
    workers/            # ProposalWorker 异步执行器(Phase 7 从 agentboard/worker 搬过来)
frontend/              # Angular 21 源码、路由、类型化 API 服务
tests/
  conftest.py           # 共享 pytest 工厂 fixture(uname/make_user/auth_headers/...)
  test_domain_boundaries.py  # Phase 2 架构边界护栏
  test_smoke.py / test_api_keys.py / test_admin_api_key_scope.py / ...
  unit/                 # 各 service 的单元测试
docs/
  refactor-plan.md      # 9 阶段垂直切片重构计划
  requirements.md       # 需求分析
  tasks.md              # 任务列表(Epic/Story/Task)
```

## 运行

```bash
pip install -r requirements.txt

# 构建 Angular（需要 Node 20.19+/22.12+/24，或直接使用 docker compose）
cd frontend
npm ci
npm run build
cd ..

# 1) 启动 REST API（默认 SQLite，端口 8000）
uvicorn agentboard.api:app --reload --port 8000

# 2) 托管 Angular 构建产物（独立服务，端口 8080）
uvicorn agentboard.web_app:app --reload --port 8080
# 浏览器打开 http://127.0.0.1:8080

# 3) 本地 MCP 服务（stdio）
#    默认调用 API：需先启动上面的 API
python -m agentboard.mcp_server

# 4) 远程 MCP（Streamable HTTP，默认要求 Bearer Token）
#    API 与 MCP 必须使用相同的 AGENTBOARD_SECRET
$env:AGENTBOARD_SECRET="replace-with-at-least-32-random-bytes"
$env:AGENTBOARD_MCP_TRANSPORT="http"
$env:AGENTBOARD_MCP_HOST="0.0.0.0"
$env:AGENTBOARD_MCP_PORT="8001"
python -m agentboard.mcp_server
# MCP endpoint: http://127.0.0.1:8001/mcp
```

### .NET 10 BFF（Stage 0+，可选）

> 2026-08 起 AgentBoard 进入"双栈 BFF"过渡期：FastAPI 仍是 AI 子系统的真源，
> .NET 10 WebAPI 接管对外 HTTP 入口并最终承载 SignalR 与通知/Webhook
> 派发。详见 `openspec/changes/dual-stack-bff-restructure/`。

```powershell
# 1) .NET 10 SDK（必须 10.0.100+，global.json 已 pin 到 10.0.301）
dotnet --version

# 2) 构建 .NET BFF（首次或 csproj 变化时）
cd dotnet
dotnet build

# 3) 启动 .NET BFF（端口 18000，host 网络监听）
$env:AGENTBOARD_DOTNET_PORT = "18000"
$env:AGENTBOARD_ENV        = "development"
$env:AGENTBOARD_SECRET     = "replace-with-at-least-32-random-bytes"
dotnet run --project src/AgentBoard.Api
# → http://localhost:18000/api/health  (返回 {"status":"ok","database":"ok",...})
# → http://localhost:18000/api/meta    (返回 6 个 snake_case enum 列表)
# → http://localhost:18000/openapi/v1.json

# 4) 跑测试
dotnet test
# 默认跑 24 个用例（Api 6 + Infrastructure 18）< 2s

# 5) 双栈一键启停（Docker Compose）
cd ..
pwsh scripts/dev-up.ps1    # 启 5 个服务：api / api-dotnet / web / mcp / db
pwsh scripts/dev-down.ps1  # 停 + 保留 volumes
pwsh scripts/dev-down.ps1 -WithVolumes  # 停 + 删 volumes
```

配置项（环境变量）：
- `AGENTBOARD_DB_URL`：数据库地址。默认 `sqlite:///./agentboard.db`；生产 `mysql+pymysql://user:pass@host:3306/agentboard`
- `AGENTBOARD_API_URL`：Web/MCP 调用的 API 地址，默认 `http://127.0.0.1:8000`
- `AGENTBOARD_MCP_TRANSPORT`：`stdio`（默认）或 `http`（Streamable HTTP）
- `AGENTBOARD_MCP_HOST` / `AGENTBOARD_MCP_PORT` / `AGENTBOARD_MCP_PATH`：远程 MCP 监听配置，默认 `127.0.0.1:8001/mcp`
- `AGENTBOARD_MCP_REQUIRE_AUTH`：远程 MCP Bearer 鉴权，当前默认关闭；需要时显式设为 `1` 开启
- `AGENTBOARD_MCP_TOKEN`：stdio + API 后端调用受保护 REST 时使用的登录 Token
- `AGENTBOARD_REQUIRE_AUTH`：设为 `1` 时统一保护 REST 业务端点
- `AGENTBOARD_ALLOW_REGISTRATION`：设为 `0` 时仅允许创建首个用户，之后注册返回 403；当前 Docker 配置为方便测试保持开启，生产应设为 `0`
- `AGENTBOARD_TOKEN_TTL_SECONDS`：Token 有效期，默认 172800 秒（2 天）
- `AGENTBOARD_SECRET`：登录 Token 签名密钥（HMAC）。默认内置不安全占位值，**生产务必设置**。
- `AGENTBOARD_ENV`：环境标识；设为 `production` 时强制检查 REST 鉴权、强密钥和 CORS 白名单。
- `AGENTBOARD_CORS_ORIGINS`：逗号分隔的 Web 来源白名单；本地默认 `*`，生产必须显式配置。

## 鉴权（注册 / 登录）

内置轻量鉴权（无额外依赖；新注册密码至少 8 位，密码使用可升级轮次的 PBKDF2 哈希，Token 为带过期时间的 HMAC 签名无状态 Bearer）：

- `POST /api/auth/register`：`{"username","password"}` → `201` 返回 `{id,username,token}`；重复用户名 → `409`
- `POST /api/auth/login`：`{"username","password"}` → `200` 返回 `{id,username,token}`；凭据错误 → `401`
- `GET /api/auth/me`：带 `Authorization: Bearer <token>` → `200` 返回当前用户；缺失/伪造 → `401`
- `PATCH /api/auth/me`：更新 `display_name`、`email`、`avatar_url`
- `POST /api/auth/change-password`：校验当前密码后更新密码
- `GET /api/users/me/projects`：返回当前用户创建（Owner）或参与（Member）的项目
- `/api/api-keys`：管理用户自己的 API Key；REST 只读请求要求 `api:read`，写请求要求 `api:write`（也支持 `api:*`）

> 本地默认保持 CRUD 开放；远程部署设置 `AGENTBOARD_REQUIRE_AUTH=1`。注册、登录和 `/api/meta` 保持公开。远程 MCP 默认始终要求同一枚 Bearer Token。

## 远程部署与 Agent 接入

### 生产环境部署前必读（安全检查清单）

> **P0 整改 B-A5 / Story 291 / Epic 145**：代码默认值对本地开发友好（`REQUIRE_AUTH=0` / `ALLOW_REGISTRATION=1` / `CORS=*` / `ENV=development`），但**绝不能原样用于生产**。启动时 `validate_runtime_security()` 会按以下清单 fail-fast。

部署到任何可被非可信网络访问的环境前，**必须**完成以下检查：

| 检查项 | 环境变量 | 要求 | 不满足的后果 |
|--------|----------|------|--------------|
| 运行环境 | `AGENTBOARD_ENV` | `production` | 不触发安全检查（dev/staging 仅 WARNING 日志） |
| HMAC 密钥 | `AGENTBOARD_SECRET` | ≥ 32 字节强随机值；API 与 MCP 必须相同 | 启动 raise `RuntimeError` |
| 鉴权开关 | `AGENTBOARD_REQUIRE_AUTH` | `1` | 启动 raise `RuntimeError`（匿名可调任意 CRUD） |
| CORS 白名单 | `AGENTBOARD_CORS_ORIGINS` | 具体域名列表，**禁止 `*`** | 启动 raise `RuntimeError` |
| 注册开关 | `AGENTBOARD_ALLOW_REGISTRATION` | `0`（首个账号注册后） | 启动 WARNING（非阻断，维护窗口临时开 `1`） |
| MCP 鉴权 | `AGENTBOARD_MCP_REQUIRE_AUTH` | `1`（强烈建议） | MCP 匿名可调（身份错位风险，见项目记忆 C1） |

快速生成强随机密钥：

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

**推荐流程**：直接复制生产模板，替换所有 `replace-with-*` 占位符：

```bash
cp .env.production .env
# 编辑 .env，逐一替换占位符为强随机值
# 特别注意：AGENTBOARD_SECRET、MARIADB_PASSWORD、MARIADB_ROOT_PASSWORD 必须独立生成
docker compose up -d --build
```

**维护窗口注册新 Agent 账号**（详见下方「获取 Agent Token」）：

1. 临时设置 `AGENTBOARD_ALLOW_REGISTRATION=1` 并重启 API
2. 调用 `POST /api/auth/register` 创建账号
3. **立即**改回 `AGENTBOARD_ALLOW_REGISTRATION=0` 并重启

> ⚠️ `validate_runtime_security()` 在 `AGENTBOARD_ENV != production` 时**不 raise**，仅记录 WARNING 日志列出当前活跃的不安全默认值。本地开发可忽略这些 WARNING；生产环境必须确保启动日志中**不出现**上述 WARNING（`ALLOW_REGISTRATION=1` 的维护窗口除外）。

### Docker Compose

先生成并设置强随机密钥，再启动 API、Web 和 MCP：

```powershell
Copy-Item .env.production .env
# 编辑 .env，把 AGENTBOARD_SECRET 换成：
python -c "import secrets; print(secrets.token_hex(32))"
docker compose up -d --build
```

生产部署的 `.env` 必须同时设置 `AGENTBOARD_WEB_API_URL`、`AGENTBOARD_CORS_ORIGINS`、`MARIADB_PASSWORD` 和 `MARIADB_ROOT_PASSWORD`；Compose 默认以 `AGENTBOARD_ENV=production`、`AGENTBOARD_ALLOW_REGISTRATION=0` 启动。宿主机端口为 API `18000`、MCP `18001/mcp`、Web `28080`，MariaDB 仅绑定 `127.0.0.1:13306`。

容器内部端口：API `8000`、MCP `8001/mcp`、Web `8080`。生产环境应由 Nginx/Caddy/云网关终止 TLS，只向 Agent 暴露 `https://.../mcp`；不要在公网使用明文 HTTP，也不要直接暴露数据库。Nginx 样例见 [examples/nginx-agentboard.conf](examples/nginx-agentboard.conf)，其中已关闭 MCP 响应缓冲并转发 Authorization。

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

#### Worker 端拉起 Codex

`CodexLauncher` 已注册到 `agentboard.executor.ADAPTERS`（OpenSpec change
`epic78-story102-cli-launcher` + `agent-integration-codex-minimax-e2e`）。
跑一个 Agent run：

```bash
# 1. 装 OpenAI Codex CLI（首次）
npm i -g @openai/codex   # 或官方安装方式

# 2. 启动 executor daemon（指定 codex 为默认 agent）
AGENTBOARD_DEFAULT_AGENT=codex \
AGENTBOARD_CODEX_BIN="codex exec --json" \
  python -m agentboard.executor --loop
```

> 端到端验证：见 `tests/test_codex_e2e.py`（fake codex CLI 模拟真实协议）。

### MiniMax（直打 chat API 路径）

`MiniMaxLauncher` 已注册到 `agentboard.executor.ADAPTERS`（2026-08-13），内部
桥接 `scripts/minimax_invoker.py`（直打 `api.minimaxi.com/v1`，绕开 minimax-cli
v1.0.1 的 MCP HTTP 缺陷）。

```powershell
# 1. 准备 Token Plan Key（sk-cp- 开头；普通 API Key 会被 402 余额不足挡）
$env:MINIMAX_API_KEY="sk-cp-REPLACE_WITH_TOKEN_PLAN_KEY"
$env:MINIMAX_BASE_URL="https://api.minimaxi.com/v1"   # 国内平台
$env:MINIMAX_MODEL="MiniMax-M2"                       # 国内模型名

# 2. 启动 executor daemon
AGENTBOARD_DEFAULT_AGENT=minimax \
  python -m agentboard.executor --loop
```

`AgentSchedule.agent = "minimax"` 走 MiniMax；`agent = "codex"` 走 Codex；
`agent = "claude"` 走 Claude Code。同一执行器、按 schedule 字段分发。

> 详细背景与踩坑：`docs/minimax-code-integration.md`（包含 CLI 路径与直打
> API 路径的对比）。端到端验证：`tests/test_minimax_e2e.py` + 
> `tests/test_minimax_invoker_unit.py`（fake HTTP server 模拟 MiniMax API）。

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
# Web 测试需要先执行 frontend 的 npm run build
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
