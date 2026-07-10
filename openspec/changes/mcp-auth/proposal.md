# Change: 实现 MCP（鉴权集成 + 运维化）

## Why
`agentboard/mcp_server.py` 已完整实现 FastMCP 工具集（CRUD / spec / 搜索 / 状态 / `spec_proposal`，api + db 双后端）。但：
1. **不鉴权感知**：AI Agent 无法通过 MCP 注册 / 登录 / 查询当前用户；调用受保护端点时无 token 透传。
2. **运维化程度低**：无标准启动脚本、无客户端配置样例（Claude Desktop / CodeBuddy）、README 仅有最简说明，难以"开箱即用"。

本次使现成的 MCP 服务**连通鉴权并生产可用**。

## What Changes
- 新增 MCP 用户管理工具：`auth_register` / `auth_login` / `auth_me`（api 与 db 双后端）。
- `api` 后端可选从 `AGENTBOARD_MCP_TOKEN` 透传 `Authorization`，以便调用受保护端点（配合 `auth` 变更的 `AGENTBOARD_REQUIRE_AUTH`）。
- 新增启动脚本与客户端配置样例 + README 章节 + MCP 冒烟测试。

## Impact
- `mcp_server.py` 新增少量工具与 `_http` 增强；不改变既有工具契约。
- 新增 `scripts/` 或 `examples/` 配置文件、测试；不影响 REST / Web。

## Status
Draft（待实现）
