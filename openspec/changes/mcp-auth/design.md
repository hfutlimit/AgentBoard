# Design — 实现 MCP（鉴权集成 + 运维化）

## 新增 MCP 工具（`mcp_server.py`）
- `auth_register(username, password)` → `{id, username, token}`（api：`POST /api/auth/register`；db：`service.register_user` + `auth.make_token`）。
- `auth_login(username, password)` → `{id, username, token}`。
- `auth_me(token)` → `{id, username}`（api：`GET /api/auth/me` 带 Bearer；db：`auth.parse_token` + `service.get_user`）。

## Token 透传（api 后端）
- `_http` 读取可选环境变量 `AGENTBOARD_MCP_TOKEN`；若存在则在请求头加 `Authorization: Bearer <token>`。
- 使 MCP 在 `AGENTBOARD_REQUIRE_AUTH=1` 场景下可作为已认证用户调用 CRUD 端点。

## 启动脚本
- 复用 `python -m agentboard.mcp_server`（stdio）；在 README 写清。
- 可选新增 `scripts/run_mcp.py` 封装环境变量后调用 `mcp.run()`（若需默认注入后端/地址）。

## 客户端配置样例（`examples/`）
- `claude_desktop_mcp.json`：stdio 启动 `python -m agentboard.mcp_server`，env 含 `AGENTBOARD_MCP_BACKEND` / `AGENTBOARD_API_URL` / `AGENTBOARD_DB_URL`。
- `codebuddy_mcp.json`：同上结构，便于 CodeBuddy 接入。

## 冒烟测试（`tests/test_mcp_smoke.py`）
- 用 `fastmcp.Client`（内存或 stdio 传输）启动服务，调用 `auth_register` → `auth_me` → `list_projects` / `create_project`，断言返回结构。
- 可用 `AGENTBOARD_MCP_BACKEND=db` 跑（无需 API），与 `test_smoke.py` 一致。

## 文档
- README「运行」补 MCP 段：两种后端、token 透传、客户端配置示例路径。
- 实现完成后更新 `openspec/specs/agentboard/spec.md` 的 MCP 工具清单。
