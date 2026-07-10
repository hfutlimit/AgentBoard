# Design — MariaDB 接入 + Alembic + MCP 工具补全

## 存储层
- 维持 `AGENTBOARD_DB_URL` 单一入口，engine 工厂已按 `sqlite://` 前缀决定是否加 `check_same_thread`。
- MariaDB 使用 `mysql+pymysql`，需保证 `pymysql` 已装（已在 requirements）。
- 索引/外键：当前外键仅逻辑级，MariaDB 下建议显式确认 `ON DELETE` 行为（现由 service 手工级联）。

## 迁移策略
- 引入 Alembic，`env.py` 直接 `from agentboard.database import engine` 作为 `target_metadata` 的来源，避免重复配置 URL。
- 初始迁移用 `op.create_table` 描述当前模型；`source_spec_id` 作为普通 `Integer`（无 FK，规避 ALTER 加外键的兼容问题）。
- 保留 `_ensure_migrations` 作为无 Alembic 运行时的兜底，但正式流程以 Alembic 为准。

## MCP 补全
- api 后端：复用 REST 新增端点；db 后端：复用 `service.update_epic/update_story/delete_*` 等既有函数（已存在于 service 层）。
- 分页：列表查询增加 `limit`/`offset`，service 层 `list_*`、`search_tasks` 接受并透传。
