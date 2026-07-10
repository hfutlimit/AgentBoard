# Tasks — 实现 MCP（鉴权集成 + 运维化）

## 1. MCP 用户管理工具
- [ ] `mcp_server.py` 新增 `auth_register` / `auth_login` / `auth_me`（api 与 db 双后端实现）。

## 2. Token 透传
- [ ] `api` 后端 `_http` 支持从 `AGENTBOARD_MCP_TOKEN` 注入 `Authorization`（可选，配合 `auth` 变更强制鉴权开关）。

## 3. 启动与客户端配置
- [ ] 确认 / 新增 `scripts/run_mcp.py` 封装环境变量后 `mcp.run()`（或仅在 README 写清 `python -m agentboard.mcp_server`）。
- [ ] 新增 `examples/claude_desktop_mcp.json` 与 `examples/codebuddy_mcp.json`（含 env：后端 / API_URL / DB_URL）。

## 4. 冒烟测试
- [ ] 新增 `tests/test_mcp_smoke.py`：用 `fastmcp.Client` 调用 `auth_register` / `auth_me` / `list_projects` / `create_project`，断言返回结构（`AGENTBOARD_MCP_BACKEND=db`）。

## 5. 文档
- [ ] README「MCP 运行与接入」章节：两种后端、token 透传、客户端配置示例。
- [ ] 实现完成后更新 `openspec/specs/agentboard/spec.md` 的「MCP 工具」清单。
