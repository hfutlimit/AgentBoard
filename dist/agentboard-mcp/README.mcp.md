# AgentBoard MCP 部署包

FastMCP Streamable-HTTP 服务，作为 Windows 服务（NSSM）跑在 `127.0.0.1:8001`，由 IIS 反向代理对外暴露为 `/mcp`。MCP 通过本机 `http://127.0.0.1:8000` 调用 WebAPI。

## 部署步骤
1. **先部署并启动 WebAPI 包**，并确保已存在一名管理员用户。
2. 复制 `env.mcp.example` 为 `.env`：
   - `AGENTBOARD_SECRET` 必须与 WebAPI **完全一致**。
   - `AGENTBOARD_MCP_TOKEN` 先用占位符。
3. 生成 MCP 专用长期 API Key（需要 WebAPI 已用同一份 `.env` 的库配置运行过 `run-webapi.ps1` 以建好 `.venv`）：
   ```powershell
   .venv\Scripts\python.exe make-mcp-token.py
   # 输出形如 MCP_API_KEY=abk_xxxx
   ```
   把 `abk_xxxx` 填入本包 `.env` 的 `AGENTBOARD_MCP_TOKEN`。
4. 以**管理员** PowerShell 运行（注意用 MCP 专属参数）：
   ```powershell
   .\install-service.ps1 -ServiceName AgentBoard-MCP -RunScript run-mcp.ps1 -DisplayName "AgentBoard MCP" -Description "AgentBoard MCP (FastMCP Streamable HTTP)"
   ```
5. 验证：`curl http://127.0.0.1:8001/mcp` 应返回 MCP 协议响应（非 404）。

> 说明：MCP 默认不开传输层鉴权（`AGENTBOARD_MCP_REQUIRE_AUTH=0`），其访问 WebAPI 的凭证就是上面的 `abk_` API Key；外部访问面由 IIS 的 `/mcp` 反代规则与防火墙控制。
