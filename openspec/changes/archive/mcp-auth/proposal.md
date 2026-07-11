# Change: 实现 MCP（鉴权集成 + 运维化）

## Why
`agentboard/mcp_server.py` 已实现基础 FastMCP 工具集（spec / 搜索 / 状态 / `spec_proposal`，api + db 双后端）。但：
1. **不鉴权感知**：AI Agent 无法通过 MCP 注册 / 登录 / 查询当前用户；调用受保护端点时无 token 透传。
2. **运维化程度低**：无标准启动脚本、无客户端配置样例（Claude Desktop / CodeBuddy）、README 仅有最简说明，难以"开箱即用"。
3. **不可远程共享**：入口只运行 stdio；不同机器上的 Agent 无法通过统一 URL 连接。
4. **项目树工具不完整**：Project 缺少 get/update/delete，Epic/Story/Task 缺少列表或删除等操作。

本次使现成的 MCP 服务**连通鉴权并生产可用**。

## What Changes
- 新增 MCP 用户管理工具：`auth_register` / `auth_login` / `auth_me`（api 与 db 双后端）。
- 登录 Token 增加有效期；REST 可通过 `AGENTBOARD_REQUIRE_AUTH=1` 统一保护业务端点。
- MCP 增加 Streamable HTTP 运行模式，并使用同一 Bearer Token 保护远程 MCP 端点。
- `api` 后端优先透传当前远程 MCP 请求的 Token，stdio 场景回退到 `AGENTBOARD_MCP_TOKEN`。
- 补齐 Project / Epic / Story / Task 的列表、获取、更新与删除工具，以及 `append_task_spec`。
- 新增启动脚本与客户端配置样例 + README 章节 + MCP 冒烟测试。

## Impact
- `mcp_server.py` 新增远程传输、鉴权、完整工具与 `_http` 增强；保留既有工具契约。
- `api.py` 可选强制业务鉴权；默认关闭以兼容本地开发，Docker 远程部署默认开启。
- 新增客户端配置、远程协议测试与部署说明。

## Status
Implemented（完整测试通过，待归档）
