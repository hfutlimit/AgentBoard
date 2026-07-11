# Change: MariaDB 脚本 + Alembic 迁移 + 集成

## Why
当前生产 MariaDB 接入已大半就绪：`AGENTBOARD_DB_URL` 切换、`pymysql` 依赖、Alembic 迁移（初始 + `users` 表）、docker-compose `db` profile、`_ensure_migrations` 兜底。但仍缺：
1. **一份独立、可审阅的 MariaDB `schema.sql` 脚本**（建库 / 建表 / 索引 / 字符集 / 授权），便于 DBA 评审与容器初始化，与 Alembic 形成"离线脚本 + 在线迁移"双路径。
2. **真实 MariaDB 11 下的验证**（Alembic `upgrade head` 与 service 冒烟）。
3. **集成冒烟测试**与 docker-compose 对接示例。

## What Changes
- 新增 `scripts/mariadb/schema.sql`：与 `models.py` 完全对齐的建库 / 建表脚本。
- 验证 Alembic 在真实 MariaDB 下可应用、功能与 SQLite 一致。
- 完善 docker-compose `db` 与 API 的对接示例；新增可选集成测试。

## Impact
- 无破坏性；新增开发/运维文件，不改变运行期代码契约。
- API / Web 行为不变；MCP 工具集已在上一轮补全。

## Status
Completed（Alembic + MCP 工具已就绪；`schema.sql` 独立脚本已补齐并经 MariaDB 11 真实验证 + 集成测试）
