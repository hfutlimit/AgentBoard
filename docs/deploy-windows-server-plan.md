# AgentBoard Windows Server 部署计划

本文用于指导在 Windows Server 上拉取 AgentBoard 源代码，并部署 Web、WebAPI 与 MCP 服务。

推荐使用项目现有部署能力对应的原生 Windows 架构：

```text
浏览器 / MCP 客户端
          │ HTTPS 443
          ▼
        IIS
   ┌──────┼────────┐
   │      │        │
静态 Web  /api/*   /mcp*
          │        │
          ▼        ▼
     WebAPI:8000  MCP:8001
          │        │
          └───┬────┘
              ▼
           MariaDB
```

- IIS 提供 HTTPS、静态 Web、SPA 路由回退和反向代理。
- WebAPI 与 MCP 只监听 `127.0.0.1`，由 NSSM 托管为 Windows 服务。
- MariaDB 保存业务数据。
- WebAPI 和 MCP 不直接暴露内部端口，外部流量只进入 IIS 的 443 端口。

仓库中已有更偏操作手册形式的 [Windows + IIS 部署指南](deploy-windows-iis.md)。正式部署时应同时参考该文档和本文的发布、备份与回滚要求。

## 1. 上线前必须完成的整改

当前仓库已经包含 Windows 部署包生成脚本，但正式上线前需要验证并处理以下事项。

### 1.1 修正前端 API 基址

前端 `ApiService` 的请求路径已经包含 `/api/...`，而当前 `configure-api-url.ps1` 默认将基址写成 `/api`，可能形成 `/api/api/...`。

同域部署时应让 `window.AGENTBOARD_API` 使用空字符串或网站根地址。例如：

```html
<script>
  window.AGENTBOARD_API = '';
</script>
```

修正后必须在浏览器网络面板确认实际请求为：

```text
https://agentboard.example.com/api/...
```

而不是：

```text
https://agentboard.example.com/api/api/...
```

### 1.2 开启 MCP 传输层鉴权

若 IIS 将 `/mcp` 暴露给外部 MCP 客户端，生产配置必须设置：

```env
AGENTBOARD_MCP_REQUIRE_AUTH=1
```

外部 MCP 客户端使用用户登录后获得的 Bearer Token。`AGENTBOARD_MCP_TOKEN=abk_...` 是 MCP 服务调用内部 WebAPI 时使用的长期 API Key，两者用途不同。

只有在 `/mcp` 完全限制在可信内网、VPN 或严格的 IIS/IP 白名单中时，才可以评估是否关闭传输层鉴权。不得将未鉴权的 MCP 端点直接暴露到公网。

### 1.3 在 WebAPI 目录生成 MCP API Key

`make-mcp-token.py` 必须加载包含生产数据库连接的 WebAPI `.env`。因此应在 WebAPI 发布目录执行：

```powershell
Set-Location C:\AgentBoard\releases\<版本>\webapi
.\.venv\Scripts\python.exe .\make-mcp-token.py
```

不要在缺少 `AGENTBOARD_DB_URL` 的 MCP 目录生成，否则脚本可能连接默认 SQLite 数据库。

### 1.4 将附件目录移出发布目录

附件默认使用相对目录 `data/attachments`。生产环境必须配置固定共享目录：

```env
AGENTBOARD_ATTACHMENT_DIR=C:\AgentBoard\shared\attachments
```

这样发布新版本或回滚代码时不会覆盖附件。

### 1.5 检查 IIS MIME 配置

部分 IIS 版本已经在服务器级注册 `.js`、`.css`、`.json` 等 MIME 类型。`web.config` 重复添加时可能产生 IIS `500.19`。

部署前应在目标服务器验证配置；出现重复项时，删除站点级重复定义，或先使用 `<remove fileExtension="..." />` 再添加。

### 1.6 重新生成部署包

不要默认使用仓库里已有的 `dist/*.zip`。拉取目标 commit 后必须重新构建前端并生成部署包，确保静态文件、API、MCP 和迁移脚本属于同一 commit。

## 2. 服务器目录规划

建议使用版本化发布目录，不要直接在 Git 工作区运行生产服务：

```text
C:\AgentBoard\
├─ repo\                         Git 工作区
├─ releases\
│  ├─ <commit-id-1>\
│  └─ <commit-id-2>\
│     ├─ webapi\
│     ├─ mcp\
│     └─ wwwroot\
├─ shared\
│  ├─ config\
│  │  ├─ webapi.env
│  │  └─ mcp.env
│  └─ attachments\
├─ logs\
└─ backups\
```

目录用途：

- `repo`：只用于 Git 拉取、测试和构建。
- `releases/<commit-id>`：不可变的版本化发布内容。
- `shared/config`：生产密钥和环境变量，不提交 Git。
- `shared/attachments`：跨版本共享的附件。
- `logs`：WebAPI、MCP 和发布日志。
- `backups`：数据库和附件备份。

生产 `.env` 应使用 NTFS ACL，仅允许管理员和运行服务的账号读取。

## 3. 服务器一次性准备

推荐环境：

| 组件 | 建议版本或要求 | 用途 |
|---|---|---|
| Windows Server | 2019 或 2022 | 生产操作系统 |
| Git for Windows | 当前稳定版 | 拉取代码 |
| Python | 3.13 x64 | WebAPI 与 MCP |
| Node.js | 22 LTS | 构建 Angular Web |
| MariaDB | 11 | 生产数据库 |
| IIS | 启用静态内容等功能 | Web 与统一入口 |
| URL Rewrite | IIS 模块 | 路径改写 |
| ARR | IIS 模块 | 反向代理 |
| NSSM | 当前稳定版 | Python Windows 服务托管 |
| TLS 证书 | 受信任证书 | HTTPS |

安装 Python 时加入 `PATH`。安装 IIS URL Rewrite 和 ARR 后，在 IIS 服务器节点中进入 **Application Request Routing Cache → Server Proxy Settings**，启用 **Enable proxy**。

Git 建议使用专用只读 Deploy Key，不要在服务器保存开发者个人账号凭证。

### 3.1 防火墙与端口

- 对外开放 `443`。
- 如果保留 `80`，仅用于跳转 HTTPS。
- 不对外开放 `8000`、`8001`、`3306`。
- WebAPI 与 MCP 必须监听 `127.0.0.1`。
- MariaDB 用户仅允许从 `127.0.0.1` 登录。

## 4. 首次拉取代码

以部署账号执行：

```powershell
New-Item -ItemType Directory -Path C:\AgentBoard -Force

git clone ssh://git@ssh.github.com:443/hfutlimit/AgentBoard.git C:\AgentBoard\repo
Set-Location C:\AgentBoard\repo
git checkout main
git pull --ff-only origin main

$ReleaseCommit = git rev-parse HEAD
Write-Host "准备发布 commit: $ReleaseCommit"
```

要求：

- 生产仓库工作区必须保持干净。
- 更新只能使用 `git pull --ff-only`，避免服务器产生额外 merge commit。
- 正式发布应优先使用经过测试的 tag 或明确 commit，而不是不经确认地发布 `main` 最新状态。
- 每次发布都记录 commit ID、发布时间和操作人。

## 5. 构建与测试

### 5.1 创建构建环境

```powershell
Set-Location C:\AgentBoard\repo
python -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install --upgrade pip
.\.venv-build\Scripts\python.exe -m pip install -r requirements.txt
```

### 5.2 构建 Web

```powershell
Set-Location C:\AgentBoard\repo\frontend
npm ci
npm run build
```

必须使用 `npm ci`，以 `package-lock.json` 固定依赖版本。

### 5.3 执行测试

至少执行后端和 MCP 冒烟测试：

```powershell
Set-Location C:\AgentBoard\repo
$env:PYTHONPATH = "."
.\.venv-build\Scripts\python.exe -m pytest tests\test_smoke.py tests\test_mcp_smoke.py -q
```

正式生产发布建议先在 CI 或构建机执行完整测试：

```powershell
.\.venv-build\Scripts\python.exe -m pytest tests -q
```

### 5.4 生成 Windows 部署包

```powershell
Set-Location C:\AgentBoard\repo
.\.venv-build\Scripts\python.exe scripts\package_windows.py
```

生成：

- `dist\agentboard-webapi.zip`
- `dist\agentboard-mcp.zip`
- `dist\agentboard-web.zip`

### 5.5 创建版本目录

```powershell
$ReleaseCommit = git rev-parse HEAD
$ReleaseRoot = "C:\AgentBoard\releases\$ReleaseCommit"

New-Item -ItemType Directory -Path $ReleaseRoot -Force
Expand-Archive .\dist\agentboard-webapi.zip "$ReleaseRoot\webapi"
Expand-Archive .\dist\agentboard-mcp.zip "$ReleaseRoot\mcp"
Expand-Archive .\dist\agentboard-web.zip "$ReleaseRoot\wwwroot"
```

解压后检查三个目录均属于同一 commit，并保存发布记录。

## 6. MariaDB 初始化

使用不同于示例值的强密码：

```sql
CREATE DATABASE agentboard
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER 'agentboard'@'127.0.0.1'
  IDENTIFIED BY '<强随机密码>';

GRANT ALL PRIVILEGES ON agentboard.*
  TO 'agentboard'@'127.0.0.1';

FLUSH PRIVILEGES;
```

如果密码包含 `@`、`:`、`/`、`#`、`%` 等字符，写入 SQLAlchemy URL 时必须进行 URL 百分号编码。

数据库连接示例：

```env
AGENTBOARD_DB_URL=mysql+pymysql://agentboard:<编码后的密码>@127.0.0.1:3306/agentboard
```

应用启动时会执行 `alembic upgrade head`。生产升级仍应在迁移前备份数据库，并在维护窗口中检查迁移结果。

## 7. WebAPI 生产配置

在 `C:\AgentBoard\shared\config\webapi.env` 保存配置：

```env
AGENTBOARD_ENV=production
AGENTBOARD_SECRET=<至少32字节的强随机值>
AGENTBOARD_REQUIRE_AUTH=1
AGENTBOARD_DB_URL=mysql+pymysql://agentboard:<编码后的密码>@127.0.0.1:3306/agentboard
AGENTBOARD_CORS_ORIGINS=https://agentboard.example.com
AGENTBOARD_ALLOW_REGISTRATION=1
AGENTBOARD_API_PORT=8000
AGENTBOARD_TOKEN_TTL_SECONDS=2592000
AGENTBOARD_ATTACHMENT_DIR=C:\AgentBoard\shared\attachments
AGENTBOARD_ATTACHMENT_MAX_SIZE=10485760
```

首次安装时把它复制到版本目录：

```powershell
$ReleaseCommit = git -C C:\AgentBoard\repo rev-parse HEAD
$WebApiRoot = "C:\AgentBoard\releases\$ReleaseCommit\webapi"

Copy-Item C:\AgentBoard\shared\config\webapi.env "$WebApiRoot\.env"
Set-Location $WebApiRoot
.\run-webapi.ps1
```

首次前台运行用于创建虚拟环境、安装依赖、执行数据库迁移和观察错误。确认成功后按 `Ctrl+C` 停止，再安装 Windows 服务。

## 8. 创建首个管理员和 MCP API Key

1. 保持 WebAPI 的 `AGENTBOARD_ALLOW_REGISTRATION=1`。
2. 启动 WebAPI。
3. 通过 Web 或 REST 注册首个用户。首个用户会成为管理员。
4. 在 WebAPI 发布目录生成 MCP API Key：

```powershell
Set-Location C:\AgentBoard\releases\<版本>\webapi
.\.venv\Scripts\python.exe .\make-mcp-token.py
```

输出格式：

```text
MCP_API_KEY=abk_xxxx
```

5. 安全保存 `abk_...`，它只会显示一次。
6. 将共享 WebAPI 配置中的 `AGENTBOARD_ALLOW_REGISTRATION` 改为 `0`。
7. 重新复制 `.env` 并重启 WebAPI。

不要在不需要注册用户时长期开放自助注册。

## 9. MCP 生产配置

在 `C:\AgentBoard\shared\config\mcp.env` 保存：

```env
AGENTBOARD_ENV=production
AGENTBOARD_SECRET=<与WebAPI完全相同>
AGENTBOARD_MCP_TRANSPORT=http
AGENTBOARD_MCP_HOST=127.0.0.1
AGENTBOARD_MCP_PORT=8001
AGENTBOARD_MCP_PATH=/mcp
AGENTBOARD_MCP_REQUIRE_AUTH=1
AGENTBOARD_API_URL=http://127.0.0.1:8000
AGENTBOARD_MCP_TOKEN=abk_<上一步生成的API-Key>
AGENTBOARD_TOKEN_TTL_SECONDS=2592000
```

复制到版本目录：

```powershell
$ReleaseCommit = git -C C:\AgentBoard\repo rev-parse HEAD
$McpRoot = "C:\AgentBoard\releases\$ReleaseCommit\mcp"

Copy-Item C:\AgentBoard\shared\config\mcp.env "$McpRoot\.env"
Set-Location $McpRoot
.\run-mcp.ps1
```

先前台运行并确认没有鉴权、端口或 API 连接错误，再安装 Windows 服务。

## 10. 安装 Windows 服务

必须在管理员 PowerShell 中执行。

### 10.1 WebAPI

```powershell
Set-Location C:\AgentBoard\releases\<版本>\webapi
.\install-service.ps1
```

### 10.2 MCP

```powershell
Set-Location C:\AgentBoard\releases\<版本>\mcp
.\install-service.ps1 `
  -ServiceName AgentBoard-MCP `
  -RunScript run-mcp.ps1 `
  -DisplayName "AgentBoard MCP" `
  -Description "AgentBoard MCP Streamable HTTP"
```

### 10.3 NSSM 补充设置

现有安装脚本会设置自动启动和异常重启。生产环境还应使用 NSSM 配置：

- WebAPI stdout/stderr 写入 `C:\AgentBoard\logs\webapi.log`。
- MCP stdout/stderr 写入 `C:\AgentBoard\logs\mcp.log`。
- 启用日志轮转。
- MCP 设置为 WebAPI 启动后再启动，或使用延迟启动。
- 服务使用专用低权限 Windows 账号，避免使用交互式管理员账号。

服务检查：

```powershell
nssm status AgentBoard-WebAPI
nssm status AgentBoard-MCP
curl.exe http://127.0.0.1:8000/api/meta
```

直接对 `/mcp` 发普通 GET 请求可能返回 `400`、`401` 或 `406`，这不一定代表服务异常。完整验证应使用支持 Streamable HTTP 的 MCP 客户端执行 initialize 和工具发现。

## 11. IIS 部署 Web

### 11.1 创建站点

在 IIS 管理器中创建 `AgentBoard` 站点：

- 物理路径：`C:\AgentBoard\releases\<版本>\wwwroot`
- 应用程序池：无托管代码
- HTTPS 绑定：正式域名和证书
- HTTP：重定向到 HTTPS，或不提供 HTTP 绑定

### 11.2 路由规则

`web.config` 应实现：

```text
/api/*  -> http://127.0.0.1:8000/api/*
/mcp*   -> http://127.0.0.1:8001/mcp*
其他非静态文件请求 -> /index.html
```

确认服务器级 ARR 已启用代理。对 MCP Streamable HTTP 场景，还应验证 ARR 没有不合理地缓冲或提前终止长连接响应。

### 11.3 配置 Web API 基址

同域部署应把 `window.AGENTBOARD_API` 配置为站点根地址，不能形成 `/api/api`。

完成后检查 `wwwroot/index.html`，并在浏览器中确认所有 API 请求都使用：

```text
https://agentboard.example.com/api/...
```

### 11.4 HTTPS 安全项

建议配置：

- HTTP 强制跳转 HTTPS。
- HSTS。
- `X-Content-Type-Options: nosniff`。
- 合理的 Content Security Policy。
- 隐藏不必要的服务器版本信息。
- 证书到期监控。

## 12. 首次部署验收

按顺序执行以下检查：

| 检查项 | 操作 | 期望结果 |
|---|---|---|
| WebAPI 本机存活 | `curl.exe http://127.0.0.1:8000/api/meta` | 返回 JSON |
| 数据库迁移 | 查看 WebAPI 日志和 `alembic_version` | 无迁移错误 |
| MCP 端口 | 检查服务状态和日志 | 监听 `127.0.0.1:8001` |
| IIS API 代理 | 打开 `https://域名/api/meta` | 返回 JSON |
| Web 静态资源 | 打开站点根目录 | 登录页正常加载 |
| Web 鉴权 | 登录管理员账号 | 登录成功 |
| 基本业务 | 创建 Project、Epic、Story、Task | CRUD 正常 |
| 附件 | 上传并下载测试文件 | 文件位于共享附件目录 |
| MCP 未授权访问 | 不带 Token 初始化 | 返回 401 |
| MCP 授权访问 | 带 Bearer Token 初始化并列出工具 | 可看到 AgentBoard 工具 |
| MCP 调用 API | 调用 `list_projects` | 返回项目列表 |
| 内部端口 | 从外部访问 8000/8001/3306 | 无法连接 |
| 服务自启 | 重启服务器 | WebAPI、MCP 自动恢复 |

验收通过后再开放正式用户访问。

## 13. 日常发布流程

每次发布按以下顺序执行：

1. 确定发布 tag 或 commit。
2. 检查服务器 Git 工作区没有未提交修改。
3. 执行数据库和附件备份。
4. `git fetch --tags` 和 `git pull --ff-only`。
5. 执行 `npm ci`、Web 构建和测试。
6. 重新生成三个 Windows 部署包。
7. 解压到新的 `releases/<commit-id>`。
8. 复制 WebAPI 和 MCP 的共享配置。
9. 创建新虚拟环境并安装依赖。
10. 在维护窗口执行数据库迁移。
11. 停止 MCP，再停止 WebAPI。
12. 把 NSSM AppDirectory 和 IIS 物理路径切换到新版本。
13. 启动 WebAPI，再启动 MCP。
14. 执行完整验收。
15. 记录发布结果，并保留最近 2～3 个可用版本。

不要直接在旧 release 目录解压覆盖，也不要把 Git 工作区作为 IIS 或 NSSM 的生产运行目录。

## 14. 备份策略

至少备份：

- MariaDB `agentboard` 数据库。
- `C:\AgentBoard\shared\attachments`。
- WebAPI 和 MCP 生产环境配置。
- 当前运行 commit ID 和发布记录。

数据库备份示例：

```powershell
mariadb-dump.exe `
  --host=127.0.0.1 `
  --user=agentboard `
  --password `
  --single-transaction `
  --routines `
  --events `
  agentboard > C:\AgentBoard\backups\agentboard.sql
```

建议：

- 每日自动备份。
- 发布前额外备份。
- 备份文件加密并复制到另一台机器或受控对象存储。
- 设置保留周期和磁盘空间告警。
- 定期执行恢复演练，而不只是确认备份任务“成功”。

## 15. 回滚计划

### 15.1 仅代码回滚

如果新版本没有执行不兼容的数据库迁移：

1. 停止 MCP 和 WebAPI。
2. 将 NSSM AppDirectory 切回上一 release。
3. 将 IIS 物理路径切回上一 release 的 `wwwroot`。
4. 启动 WebAPI 和 MCP。
5. 执行验收。

### 15.2 包含数据库迁移的回滚

数据库迁移不一定可以通过切换代码自动回滚。如果新 migration 与旧代码不兼容：

1. 停止所有应用服务。
2. 恢复升级前 MariaDB 备份。
3. 必要时恢复升级前附件快照。
4. 将代码和 IIS 切回旧 release。
5. 启动服务并验收数据完整性。

任何生产 migration 上线前都应明确回答：是否向后兼容、是否支持 downgrade、失败时恢复时间需要多久。

## 16. 监控与维护

建议持续监控：

- `AgentBoard-WebAPI`、`AgentBoard-MCP` Windows 服务状态。
- `https://域名/api/health` 或 `/api/meta`。
- HTTP 5xx、401、429 数量。
- WebAPI、MCP、IIS 日志增长和异常。
- MariaDB 连接数、慢查询、磁盘空间和备份结果。
- 附件目录容量。
- TLS 证书到期时间。
- Windows 更新和服务器重启后的服务恢复情况。

## 17. 建议实施节奏

可以分三个阶段实施：

1. **服务器准备**：安装运行环境、MariaDB、IIS、ARR、URL Rewrite、NSSM 和证书。
2. **预生产部署**：修正上线阻塞项，完成构建、服务安装、IIS 配置和功能验收。
3. **正式切换**：备份、发布目标 commit、开放 443、完成重启与回滚演练。

在首次正式上线前，至少安排一次完整的“发布新版本 → 验收 → 切回旧版本 → 恢复新版本”演练。
