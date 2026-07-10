# Change: 前端注册 / 登录集成（鉴权 UI）

## Why
后端鉴权已就绪：`models.User`、`agentboard/auth.py`（pbkdf2 哈希 + HMAC 无状态 Token）、`service.register_user/authenticate_user/get_user`、`api.py` 的 `/api/auth/register|login|me`，以及 `users` 表 Alembic 迁移、后端测试（`test_backend_flow.py` / `test_web_flow.py`）。

但 **Web SPA 完全没有登录界面、token 持久化与受保护路由**：用户无法在浏览器里注册 / 登录，AI Agent 之外的人类用户没有可用的鉴权入口。本次补齐"前端最后一公里"。

## What Changes
- `web/static/index.html` / `app.js` / `style.css`：新增登录 / 注册界面、token 生命周期（`localStorage`）、`fetch` 自动携带 `Authorization`、启动守卫、登出、顶部用户信息。
- 后端契约**不变**（端点已存在）；仅可选增加 `AGENTBOARD_REQUIRE_AUTH` 开关以强制 CRUD 鉴权（默认关闭，与 MCP / Web 兼容）。

## Impact
- 仅前端静态资源变更，不影响 REST / MCP 现有行为。
- 现有后端测试继续有效；UI 流由 Playwright 变更（`playwright-e2e`）补充覆盖。
- 无破坏性；多用户数据隔离由 `users` 表 + token 承载（本期 CRUD 仍为单命名空间，不做按用户过滤）。

## Status
Draft（待实现）
