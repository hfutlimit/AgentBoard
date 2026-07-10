# Change: MariaDB 实际接入 + Alembic 迁移 + MCP 工具补全

## Why
当前生产 MariaDB 尚未实际接入，schema 演进依赖 `database._ensure_migrations` 的在线 `ALTER TABLE`。为达到生产可用与规范迁移，需要：
1. 真正验证 MariaDB 连接与 DDL 兼容性；
2. 用 Alembic 接管迁移（替代临时在线 ALTER）；
3. 补全 MCP 工具（更新/删除 epic、story、分页等），与 REST API 对齐。

## What Changes
- 新增 Alembic 环境（`migrations/`），`env.py` 读取 `AGENTBOARD_DB_URL`，初始迁移覆盖全部表 + `source_spec_id`。
- `init_db` 的在线迁移降级为「无 Alembic 时的兼容保底」。
- MCP 新增：`update_epic` / `delete_epic` / `update_story` / `delete_story` / `get_epic` / `get_story`，以及列表分页参数。

## Impact
- 无破坏性；新增开发期依赖 `alembic`。
- API/Web 行为不变；仅 MCP 工具集更完整。
- 需用户提供可用的 MariaDB 连接信息（见 tasks）。

## Status
Draft（待实现）
