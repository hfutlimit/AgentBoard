# Design — 实现 MCP（鉴权集成 + 运维化）

## 新增 MCP 工具（`mcp_server.py`）
- `auth_register(username, password)` → `{id, username, token}`（api：`POST /api/auth/register`；db：`service.register_user` + `auth.make_token`）。
- `auth_login(username, password)` → `{id, username, token}`。
- `auth_me(token)` → `{id, username}`（api：`GET /api/auth/me` 带 Bearer；db：`auth.parse_token` + `service.get_user`）。

## Token 透传（api 后端）
- `_http` 优先读取当前 HTTP MCP 请求的访问令牌；stdio 无请求上下文时读取 `AGENTBOARD_MCP_TOKEN`。
- 使 MCP 在 `AGENTBOARD_REQUIRE_AUTH=1` 场景下可作为已认证用户调用 CRUD 端点。

## 远程 MCP 鉴权与传输
- `AGENTBOARD_MCP_TRANSPORT=stdio|http`，HTTP 使用 MCP Streamable HTTP，默认路径 `/mcp`。
- `AGENTBOARD_MCP_REQUIRE_AUTH=1` 时，FastMCP TokenVerifier 使用 AgentBoard HMAC Token 校验请求。
- REST 登录 Token 包含版本、用户 id、过期时间和 HMAC 签名；有效期由 `AGENTBOARD_TOKEN_TTL_SECONDS` 控制。
- 远程客户端先调用 REST `POST /api/auth/login`，再以 `Authorization: Bearer <token>` 连接 MCP。
- 生产部署必须经 HTTPS 反向代理暴露 MCP URL，不直接公网暴露明文 HTTP。

## 完整项目树工具
- Project：list/get/create/update/delete。
- Epic：按 project list，加 get/create/update/delete。
- Story：按 epic list，加 get/create/update/delete。
- Task：按 story list，加 search/get/create/update/delete、状态与 spec 操作。

## 启动
- 复用 `python -m agentboard.mcp_server`；通过环境变量选择 stdio 或 http、host、port、path。
- Docker Compose 增加独立 `mcp` 服务，与 API 共享数据库和 `AGENTBOARD_SECRET`。

## 客户端配置样例（`examples/`）
- 提供 stdio JSON 示例与远程 URL + Bearer Token 示例；不同客户端只需映射 URL 和令牌字段。

## 冒烟测试（`tests/test_mcp_smoke.py`）
- 用 `fastmcp.Client` 验证工具发现、完整项目树 CRUD、`auth_register` → `auth_me`。
- 启动真实 HTTP MCP 子进程，断言无 Token 被拒绝、有效 Token 可连接并调用工具。
- 可用 `AGENTBOARD_MCP_BACKEND=db` 跑（无需 API），与 `test_smoke.py` 一致。

## 文档
- README「运行」补 MCP 段：两种后端、token 透传、客户端配置示例路径。
- 实现完成后更新 `openspec/specs/agentboard/spec.md` 的 MCP 工具清单。
