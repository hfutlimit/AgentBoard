# AgentBoard Web 部署包

Angular 生产构建的静态产物。部署到 IIS 站点物理路径，由 `web.config` 接管 SPA 路由回退与 `/api`、`/mcp` 反向代理。

## 部署步骤
1. 在 IIS 中新建站点（如 `AgentBoard`），物理路径指向**本目录**（即含 `index.html` 与 `web.config` 的目录）。
2. 配置 API 基址（默认同源 `/api`，与 `web.config` 反代一致）：
   ```powershell
   .\configure-api-url.ps1            # 写入 /api
   # 或显式指定：.\configure-api-url.ps1 -ApiUrl https://agentboard.example.com/api
   ```
3. 绑定 HTTPS 证书（建议），防火墙放行 443。
4. 浏览器访问站点域名，应能加载登录页。

## 反向代理前提（服务器级一次配置）
- 安装 IIS「URL Rewrite」与「Application Request Routing (ARR)」模块。
- IIS 管理器 → 服务器节点 → **Application Request Routing Cache** → **Server Proxy Settings** → 勾选 **Enable proxy**，应用。

## 文件说明
- `web.config` — SPA 回退 + `/api`、`/mcp` 反向代理规则 + MIME。
- `configure-api-url.ps1` — 注入 `window.AGENTBOARD_API`。
- `index.html` 等 — Angular 构建产物（已含主题内联样式）。
