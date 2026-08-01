# AgentBoard 本机安装说明

AgentBoard 默认使用 Docker Compose 启动 Web、REST API、MCP 和 MariaDB。仓库已经使用非默认宿主机端口，减少与本机开发服务冲突：

| 服务 | 本机地址 |
| --- | --- |
| Web | http://localhost:28080 |
| REST API | http://localhost:18000 |
| Swagger | http://localhost:18000/docs |
| MCP | http://localhost:18001/mcp |
| MariaDB | localhost:13306 |

## Docker Desktop 安装

要求：Windows 10/11、Docker Desktop（Linux containers）和 Git。

在项目根目录打开 PowerShell：

```powershell
cd E:\Projects\AgentBoard

$bytes = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
$secret = [Convert]::ToHexString($bytes).ToLowerInvariant()
"AGENTBOARD_SECRET=$secret" | Set-Content -Encoding utf8 .env

docker compose up -d --build --remove-orphans
docker compose ps
```

首次访问 http://localhost:28080。注册的第一个账号会成为管理员，API 启动时会自动执行数据库迁移。

## MCP 配置

MCP 默认要求认证。登录 Web 后创建 API Key，客户端使用：

```text
URL: http://localhost:18001/mcp
Authorization: Bearer <API Key 或登录 Token>
Transport: Streamable HTTP
```

MCP 权限与 Token 对应用户一致：管理员可以访问全部项目，普通用户只能访问自己创建或作为成员加入的项目。

## 常用命令

```powershell
# 查看状态
docker compose ps

# 查看启动日志
docker compose logs --tail 200 api web mcp db

# 停止服务并保留数据
docker compose down

# 更新代码后重建
docker compose up -d --build --remove-orphans
```

只有确认不再需要本机数据时才能执行 `docker compose down -v`，该命令会删除 MariaDB 数据卷。

## 端口冲突

```powershell
Get-NetTCPConnection -State Listen |
  Where-Object LocalPort -In 28080,18000,18001,13306 |
  Select-Object LocalAddress,LocalPort,OwningProcess
```

若仍有冲突，只修改 `docker-compose.yml` 中端口映射左侧的宿主机端口。例如将 `28080:8080` 改为 `38080:8080`；容器内部端口无需修改。

## 本机前端开发

```powershell
cd E:\Projects\AgentBoard\frontend
npm ci
npm start
```

生产构建：

```powershell
npm run build
```

`agentboard.web_app` 会优先使用 `frontend/dist/frontend/browser`，没有本机构建产物时才回退到仓库中的 `agentboard/web/static`。
