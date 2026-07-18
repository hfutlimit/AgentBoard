# AgentBoard Windows + IIS 部署指南

适用场景：将 AgentBoard 部署到一台 Windows Server，使用 **IIS 作为统一入口**（反向代理），
后端 WebAPI 与 MCP 以 **NSSM 托管的 Windows 服务** 形式跑在本机 `127.0.0.1`，数据库用 **MariaDB**。

---

## 1. 架构总览

```
                         ┌─────────────────────────────────────┐
   浏览器 / MCP 客户端 ──▶│   IIS (443/80)                      │
                         │   ├─ /            → 静态 Web (本目录) │
                         │   ├─ /api/*       → 127.0.0.1:8000   │
                         │   └─ /mcp*        → 127.0.0.1:8001   │
                         └───────────────┬───────────┬──────────┘
                                         │           │  (ARR 反向代理)
                        ┌────────────────▼──┐   ┌─────▼────────────────┐
                        │ WebAPI 服务        │   │ MCP 服务             │
                        │ uvicorn :8000      │   │ FastMCP http :8001   │
                        │ (NSSM: AgentBoard- │   │ (NSSM: AgentBoard-   │
                        │  WebAPI)           │   │  MCP)                │
                        └─────────┬──────────┘   └─────────┬───────────┘
                                  │  http://127.0.0.1:8000  │
                                  └────────────┬───────────┘
                                           ┌──▼───┐
                                           │MariaDB│  3306
                                           └───────┘
```

- **同源优势**：浏览器访问 `https://host/` 与 `/api` 同域，后端 CORS 不再必要；用户凭证经 `/api/auth` 登录获取。
- **MCP 旁路**：MCP 服务通过本机 `http://127.0.0.1:8000` 直连 WebAPI（不经 IIS），用长期 `abk_` API Key 鉴权。

---

## 2. 前置条件（服务器一次性准备）

| 组件 | 说明 | 获取 |
|---|---|---|
| Windows Server 2016+ | 已加域或独立主机 | — |
| Python 3.13 | 安装时勾选 **Add to PATH** | python.org |
| MariaDB 11（或 MySQL 8） | 数据库 | mariadb.org |
| IIS | 含「Web 服务器」角色 | 服务器管理器 |
| URL Rewrite | IIS 模块 | iis.net/downloads |
| Application Request Routing (ARR) | 反向代理 | iis.net/downloads（含 URL Rewrite 依赖） |
| NSSM | 将 Python 进程包装为 Windows 服务 | nssm.cc |
| 公网域名 + TLS 证书 | 建议 HTTPS | 自有 CA / Let's Encrypt |

> 不需要在服务器上安装 Node.js —— 前端已预构建为静态文件。

### 2.1 启用 IIS 反向代理（关键，否则 `/api`、`/mcp` 规则不生效）
1. 安装 **URL Rewrite** 与 **ARR** 模块。
2. 打开 **IIS 管理器** → 左侧选中**服务器节点**（不是站点）→
   **Application Request Routing Cache** → 右侧 **Server Proxy Settings** →
   勾选 **Enable proxy** → 右侧 **Apply**。
   （保持「Reverse rewrite host in response headers」勾选即可。）

---

## 3. 步骤一：MariaDB 建库

```sql
CREATE DATABASE agentboard CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'agentboard'@'127.0.0.1' IDENTIFIED BY 'agentboard';   -- 改成强密码
GRANT ALL PRIVILEGES ON agentboard.* TO 'agentboard'@'127.0.0.1';
FLUSH PRIVILEGES;
```
数据库迁移由应用首次启动时通过 **Alembic** 自动执行（`init_db()` 调用 `alembic upgrade head`），无需手动建表。

---

## 4. 步骤二：部署 WebAPI（REST 服务）

1. 将 `agentboard-webapi.zip` 解压到 `C:\AgentBoard\webapi\`。
2. 复制 `env.webapi.example` → `.env`，至少修改：
   - `AGENTBOARD_SECRET`：强随机，`openssl rand -hex 32`（**32 字节以上**）。
   - `AGENTBOARD_DB_URL`：`mysql+pymysql://agentboard:<密码>@127.0.0.1:3306/agentboard`。
   - `AGENTBOARD_CORS_ORIGINS`：IIS 公网域名，如 `https://agentboard.example.com`（**不可用 `*`**）。
   - `AGENTBOARD_ALLOW_REGISTRATION`：首次建管理员时临时设 `1`，建好后改回 `0`。
3. **以管理员身份**打开 PowerShell，执行：
   ```powershell
   cd C:\AgentBoard\webapi
   .\install-service.ps1
   ```
   脚本会创建 `.venv`、安装依赖，并注册自启服务 `AgentBoard-WebAPI`。
4. 验证：
   ```powershell
   curl.exe http://127.0.0.1:8000/api/meta
   ```
   应返回 JSON（`{"title":..., "version":...}` 之类）。

> 若需重装/卸载服务：`nssm stop AgentBoard-WebAPI`、`nssm remove AgentBoard-WebAPI confirm`。

---

## 5. 步骤三：生成 MCP Token 并部署 MCP 服务

MCP 服务调用 WebAPI 需要一枚长期有效的 API Key（因为 WebAPI 生产模式强制鉴权）。

1. **先确保 WebAPI 已启动且存在管理员账号**（首次可用 `AGENTBOARD_ALLOW_REGISTRATION=1` 在 Web 上注册并提升管理员）。
2. 将 `agentboard-mcp.zip` 解压到 `C:\AgentBoard\mcp\`。
3. 复制 `env.mcp.example` → `.env`：
   - `AGENTBOARD_SECRET` 必须与 WebAPI **完全一致**。
   - `AGENTBOARD_MCP_TOKEN` 先留占位符。
4. 生成 Token（复用 WebAPI 同一份库配置，确保 `.venv` 已存在）：
   ```powershell
   cd C:\AgentBoard\mcp
   .venv\Scripts\python.exe make-mcp-token.py
   # 输出：MCP_API_KEY=abk_xxxx
   ```
   把 `abk_xxxx` 填入本包 `.env` 的 `AGENTBOARD_MCP_TOKEN`。
5. 安装 MCP 服务（用 MCP 专属参数）：
   ```powershell
   .\install-service.ps1 -ServiceName AgentBoard-MCP -RunScript run-mcp.ps1 `
       -DisplayName "AgentBoard MCP" -Description "AgentBoard MCP (FastMCP Streamable HTTP)"
   ```
6. 验证：
   ```powershell
   curl.exe http://127.0.0.1:8001/mcp
   ```
   应返回 MCP 协议响应（非 404）。

---

## 6. 步骤四：部署 Web 到 IIS

1. 将 `agentboard-web.zip` 解压到网站目录，例如 `C:\AgentBoard\wwwroot\`。
2. 在 **IIS 管理器** 中：
   - 右侧 **添加网站** → 站点名 `AgentBoard`，**物理路径** 选上面的 `wwwroot` 目录。
   - 绑定：建议 `https` + 你的证书；同时保留 `http`（或仅 https，由你定）。
3. 注入 API 基址（默认同源 `/api`，与 `web.config` 反代一致）：
   ```powershell
   cd C:\AgentBoard\wwwroot
   .\configure-api-url.ps1                 # 写入 /api
   # 或：.\configure-api-url.ps1 -ApiUrl https://agentboard.example.com/api
   ```
4. `web.config` 已随包放置于物理路径根目录，IIS 会自动读取：
   - `/api/*` → `127.0.0.1:8000/api/*`
   - `/mcp*`  → `127.0.0.1:8001/mcp*`
   - 其余非文件请求 → `index.html`（SPA 路由）。
5. 浏览器访问站点域名，应能加载登录页；登录后进入项目列表。

> 若把 Web 部署在**子应用路径**（如 `/agentboard`），需额外调整 `web.config` 的 `match`/`action` 路径与前端 `base href`，本文默认站点根路径。

---

## 7. 步骤五：HTTPS、防火墙与暴露面

- **防火墙**：仅放行 **443（HTTPS）** 入站；WebAPI(8000)/MCP(8001) 仅监听 `127.0.0.1`，不对外暴露。
- **MCP 暴露**：若需让外部 MCP 客户端访问，经 IIS `/mcp` 反代即可（已配置）。传输层 `AGENTBOARD_MCP_REQUIRE_AUTH` 默认 `0`，由 IIS/网络控制暴露面；如需强制 Token，将其设为 `1` 并在客户端配置 Bearer。
- **HSTS / 证书**：在 IIS 站点绑定里选证书；如需 HSTS 可加规则（可选）。

---

## 8. 验证清单

| 检查项 | 命令 / 方式 | 期望 |
|---|---|---|
| WebAPI 存活 | `curl 127.0.0.1:8000/api/meta` | 返回 JSON |
| 数据库连通 | WebAPI 服务无报错、日志无连接异常 | 迁移自动完成 |
| MCP 存活 | `curl 127.0.0.1:8001/mcp` | 非 404 |
| 前端加载 | 浏览器开站点根 | 登录页正常 |
| 经 IIS 调 API | 浏览器开 `https://host/api/meta` | 返回 JSON（经反代） |
| 登录可用 | Web 注册/登录 | 拿到 Token，能访问项目 |

---

## 9. 常见问题排查

- **`/api` 返回 404 或 502**：ARR 代理未启用 → 回到 2.1 勾选 Enable proxy；确认 WebAPI 服务正在运行。
- **IIS 返回 500.52（URL Rewrite 错误）**：`web.config` 路径/规则写法问题；检查 `rewrite` 节 XML 是否完整。
- **MCP 调 API 全部 401**：`AGENTBOARD_MCP_TOKEN` 未填或失效；重新跑 `make-mcp-token.py` 并重启 `AgentBoard-MCP` 服务。
- **API 启动即崩溃 `production requires ...`**：`AGENTBOARD_ENV=production` 下必须满足 强密钥 + `REQUIRE_AUTH=1` + 显式 `CORS_ORIGINS`（非 `*`）。
- **MCP 流式响应被缓冲**：ARR 默认可能对 SSE 缓冲；在服务器级 ARR 设置里将 **responseBufferLimit** 调大或设为 0（仅影响流式工具输出场景）。
- **静态资源 404**：确认 `web.config` 物理路径即含 `index.html` 的目录；`configure-api-url.ps1` 已正确替换 `__API_URL__`。
- **服务启动失败**：`nssm edit AgentBoard-WebAPI` 查看 Stdout/Stderr 重定向；或在包目录手动 `.\run-webapi.ps1` 看报错。

---

## 10. 升级流程

1. 重新执行 `python scripts/package_windows.py` 生成新包（基于最新代码构建）。
2. 停止服务：`nssm stop AgentBoard-WebAPI` / `nssm stop AgentBoard-MCP`。
3. 解压覆盖 `webapi/`、`mcp/` 目录（保留各自 `.env` 与 `.venv`）。
4. 覆盖 `wwwroot/` 静态文件（重新跑 `configure-api-url.ps1`）。
5. 启动服务：`nssm start AgentBoard-WebAPI` / `AgentBoard-MCP`。
6. 数据库结构变更由 Alembic 在 WebAPI 启动时自动迁移。

---

## 11. 环境变量速查

| 变量 | WebAPI | MCP | 说明 |
|---|---|---|---|
| `AGENTBOARD_ENV` | `production` | `production` | 触发生产安全校验 |
| `AGENTBOARD_SECRET` | 强随机 | **同 WebAPI** | Token 签名密钥 |
| `AGENTBOARD_REQUIRE_AUTH` | `1` | — | 保护 REST 业务端点 |
| `AGENTBOARD_DB_URL` | `mysql+pymysql://...` | — | MariaDB 连接串 |
| `AGENTBOARD_CORS_ORIGINS` | 公网域名 | — | 生产必须显式白名单 |
| `AGENTBOARD_API_PORT` | `8000` | — | WebAPI 监听端口 |
| `AGENTBOARD_MCP_TRANSPORT` | — | `http` | Streamable HTTP |
| `AGENTBOARD_MCP_HOST/PORT/PATH` | — | `127.0.0.1`/`8001`/`/mcp` | MCP 监听 |
| `AGENTBOARD_API_URL` | — | `http://127.0.0.1:8000` | MCP→API 地址 |
| `AGENTBOARD_MCP_TOKEN` | — | `abk_...` | MCP 调 API 的 API Key |
| `AGENTBOARD_ALLOW_REGISTRATION` | 首次 `1` 后 `0` | — | 自助注册开关 |
