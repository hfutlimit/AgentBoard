# Tasks — MCP 鉴权、完整工具与远程部署

## 1. MCP 用户管理工具
- [x] `mcp_server.py` 新增 `auth_register` / `auth_login` / `auth_me`（api 与 db 双后端）。

## 2. Token 与业务鉴权
- [x] 登录 Token 增加过期时间并兼容 REST/Web/MCP 校验。
- [x] `AGENTBOARD_REQUIRE_AUTH=1` 统一保护 REST 业务端点。
- [x] `api` 后端优先透传当前 MCP Token，stdio 回退 `AGENTBOARD_MCP_TOKEN`。

## 3. 远程 MCP 与完整工具
- [x] MCP 增加 Streamable HTTP 传输及 Bearer Token 校验。
- [x] 补齐 Project/Epic/Story/Task list/get/update/delete 与 `append_task_spec`。

## 4. 启动、部署与客户端配置
- [x] `python -m agentboard.mcp_server` 支持 stdio/http 环境变量配置。
- [x] Docker Compose 增加共享 API 数据和密钥的 `mcp` 服务。
- [x] 新增 stdio 与远程 MCP 客户端配置示例。

## 5. 测试
- [x] 新增 `tests/test_mcp_smoke.py`，通过 `fastmcp.Client` 调用鉴权和完整项目树工具。
- [x] 增加真实 HTTP Bearer 鉴权测试（无 Token 拒绝，有效 Token可调用）。

## 6. 文档
- [x] README 补充远程部署、登录取 Token、不同 Agent 接入和安全说明。
- [x] 更新 `openspec/specs/agentboard/spec.md` 的 MCP 工具与鉴权清单。
