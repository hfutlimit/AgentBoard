# AgentBoard 本机安装说明

本文覆盖 AgentBoard Web、REST API、MCP、MariaDB、RabbitMQ，以及可选的 Proposal Worker。当前项目的 Docker 主机端口刻意避开常见默认端口：

| 服务 | 本机地址/端口 |
| --- | --- |
| Web | http://localhost:28080 |
| REST / Swagger | http://localhost:18000 / http://localhost:18000/docs |
| MCP (Streamable HTTP) | http://localhost:18001/mcp |
| MariaDB | localhost:13306 |
| RabbitMQ AMQP | localhost:35672 |
| RabbitMQ 管理页 | http://localhost:31567（guest / guest，仅限本机开发） |

## 方案 A：Docker Desktop（推荐）

要求：Windows 10/11、Docker Desktop（Linux containers）和 Git。

在项目根目录打开 PowerShell：

```powershell
cd E:\Projects\AgentBoard

# 生成 32 字节随机密钥并创建本机 .env；.env 已被 git 忽略。
$bytes = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
$secret = [Convert]::ToHexString($bytes).ToLowerInvariant()
"AGENTBOARD_SECRET=$secret" | Set-Content -Encoding utf8 .env

docker compose up -d --build
docker compose ps
```

首次打开 http://localhost:28080，注册的第一个账号会成为管理员。数据库迁移由 API 启动时自动执行，无需单独运行 Alembic。

### 启用 Proposal Worker

默认 Compose 会启动 RabbitMQ，但不会启动 Worker。这样即使尚未安装 LLM/Agent CLI，Project、Epic、Story、Task、Document、Comment 和 Proposal 的人工界面仍可正常使用。

Worker 从标准输入向外部 Agent 命令发送 JSON，并要求命令在标准输出返回一个 JSON 对象：

```json
{"action":"ask","questions":["目标用户是谁？"],"summary":"缺少用户范围"}
```

或：

```json
{"action":"finalize","converged_spec":"# 需求\n\n- [ ] 后端实现\n- [ ] 前端实现"}
```

先为 Worker 准备账号，并在 `.env` 添加账号、密码与 Agent 命令。命令必须存在于 Worker 容器内；如果使用宿主机上的 Codex、Claude Code 或其他 CLI，建议改用下文的“本机 Worker”方式。

```dotenv
AGENTBOARD_WORKER_USERNAME=proposal-worker
AGENTBOARD_WORKER_PASSWORD=replace-with-a-local-strong-password
AGENTBOARD_WORKER_NAME=workbuddy
AGENTBOARD_PROPOSAL_AGENT_COMMAND=python /opt/proposal-agent/agent.py
```

将你的 Agent 适配器加入镜像或挂载到 `/opt/proposal-agent` 后启动可选 profile：

```powershell
docker compose --profile proposal-worker up -d --build proposal-worker
docker compose logs -f proposal-worker
```

Worker 采用 RabbitMQ 唤醒 + 数据库待处理列表兜底；并发 Worker 通过数据库 CAS 抢占，过期租约会重新入队。无法解析的 RabbitMQ 消息会进入 dead-letter queue。

### MCP 客户端配置

1. 在 Web 登录后，从“设置 / API Keys”创建具备 `api:read`、`api:write` 的 Key，或使用登录 Token。
2. MCP URL 使用 `http://localhost:18001/mcp`。
3. Authorization 使用 `Bearer <token-or-api-key>`。

Proposal Worker 可用的 MCP tools：

- `proposal_pending`
- `proposal_claim`
- `proposal_get`
- `proposal_ask`
- `proposal_finalize`
- `proposal_fail`

Story/Task 的创建不暴露给 Proposal Worker。只有用户在 Web 最终检查 `converged_spec` 后，才可执行“创建 Story / Tasks”。规格中的 `- [ ] ...` 清单项会转换成 Task。

### 停止与清理

```powershell
# 停止容器，保留 MariaDB/RabbitMQ 数据卷
docker compose down

# 确认不再需要本机数据后再执行（会删除数据卷，不可恢复）
docker compose down -v
```

## 方案 B：Python + Node 本机运行

要求：Python 3.11+、Node.js 20.19+/22.12+/24+。未设置 `AGENTBOARD_DB_URL` 时使用项目目录中的 SQLite，适合单机开发。

```powershell
cd E:\Projects\AgentBoard
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

Push-Location frontend
npm ci
npm run build
Pop-Location

$env:AGENTBOARD_SECRET = ([guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N'))
$env:AGENTBOARD_REQUIRE_AUTH = '1'
$env:AGENTBOARD_ALLOW_REGISTRATION = '1'
```

分别打开三个 PowerShell 窗口：

```powershell
# 窗口 1：REST API
.\.venv\Scripts\Activate.ps1
uvicorn agentboard.api:app --reload --port 18000
```

```powershell
# 窗口 2：Web
.\.venv\Scripts\Activate.ps1
$env:AGENTBOARD_API_URL = 'http://localhost:18000'
uvicorn agentboard.web_app:app --reload --port 28080
```

```powershell
# 窗口 3：MCP（stdio）
.\.venv\Scripts\Activate.ps1
$env:AGENTBOARD_API_URL = 'http://localhost:18000'
$env:AGENTBOARD_MCP_TOKEN = '<登录 Token 或 API Key>'
python -m agentboard.mcp_server
```

### 本机 Worker（适合调用宿主机 Agent CLI）

本机已有 RabbitMQ 时可设 `AGENTBOARD_MQ_URL` 并使用 `--mq`；没有 RabbitMQ 时直接轮询即可：

```powershell
.\.venv\Scripts\Activate.ps1
$env:AGENTBOARD_API_URL = 'http://localhost:18000'
$env:AGENTBOARD_WORKER_USERNAME = 'proposal-worker'
$env:AGENTBOARD_WORKER_PASSWORD = '<本机 Worker 密码>'
$env:AGENTBOARD_WORKER_NAME = 'workbuddy'
$env:AGENTBOARD_PROPOSAL_AGENT_COMMAND = '<你的 Agent CLI 或适配器命令>'

# 处理一次后退出
python -m agentboard.worker --once

# 持续数据库轮询
python -m agentboard.worker

# RabbitMQ 消费模式（Docker RabbitMQ 暴露在 35672）
$env:AGENTBOARD_MQ_URL = 'amqp://guest:guest@localhost:35672/%2F'
python -m agentboard.worker --mq
```

## 端口冲突处理

检查端口占用：

```powershell
Get-NetTCPConnection -State Listen |
  Where-Object LocalPort -In 28080,18000,18001,13306,35672,31567 |
  Select-Object LocalAddress,LocalPort,OwningProcess
```

如有冲突，只修改 `docker-compose.yml` 中端口映射左侧的宿主机端口，例如把 `"28080:8080"` 改为 `"38080:8080"`；容器内端口和服务间地址无需修改。

## 常见问题

- API 启动时报 Secret 不安全：确保 `.env` 中 `AGENTBOARD_SECRET` 至少 32 个字符。
- Worker 启动即退出：需要 Token，或已注册的 `AGENTBOARD_WORKER_USERNAME` / `AGENTBOARD_WORKER_PASSWORD`，并且必须配置 Agent 命令。
- Proposal 一直是 `queued`：Worker 未启动，或 Agent 命令不可用；人工功能不受影响。
- Proposal 一直是 `analyzing`：等待租约到期，Worker 下次轮询会自动回收，也可调用 `POST /api/proposals/reclaim-stale`。
- RabbitMQ 临时不可用：API 仍会保存状态；Worker 恢复后会扫描数据库 backlog。
