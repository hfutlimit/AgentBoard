# Tasks — MariaDB 脚本 + 迁移 + 集成

## 1. Alembic 迁移（已完成）
- [x] 初始化 `alembic.ini` 与 `migrations/`（env.py 复用 `AGENTBOARD_DB_URL`）
- [x] 编写初始迁移：projects / epics / stories / tasks（含 `source_spec_id`）
- [x] `users` 表迁移（`05e694deb9d6_add_users_table`）
- [x] `database.init_db`：有 Alembic 时 `alembic upgrade head`；否则 `create_all` + 在线迁移兜底
- [x] MCP 工具补全（get/update/delete epic|story、列表分页）

## 2. 独立 MariaDB 脚本（已完成）
- [x] 新增 `scripts/mariadb/schema.sql`：建库 `agentboard`（`utf8mb4`）、建用户并授权、`projects/epics/stories/tasks/users/comments` 六表（字段/类型/索引/唯一约束与 `models.py` 对齐）、`tasks.source_spec_id`。
- [x] 在 `scripts/mariadb/README.md`（或 docker-compose 注释）说明初始化与离线评审用法。

## 3. 真实 MariaDB 验证（已完成）
- [x] 用户提供可用的 MariaDB 连接信息（`AGENTBOARD_DB_URL=mysql+pymysql://...`）
- [x] 用 MariaDB 起 `init_db`，验证 Alembic `upgrade head` 建表 DDL 兼容（字符集/索引/外键）
- [x] 在 MariaDB 下跑通 service 层冒烟（CRUD + 状态机 + 搜索 + 生成子任务）

## 4. docker-compose 对接
- [x] 更新 `docker-compose.yml`：明确 `db` profile 的 `MARIADB_DATABASE/USER/PASSWORD` 与 API 的 `AGENTBOARD_DB_URL` 对接示例（含注释）。

## 5. 集成测试
- [x] 新增 `tests/test_mariadb_integration.py`：`skipif` 无 `AGENTBOARD_TEST_MARIADB`；验证 MariaDB 下 `init_db` 与 service CRUD。

## 6. 文档
- [x] 更新 `docs/requirements.md` 的 MariaDB 接入说明（`.sql` 与 Alembic 双路径）与 README 对应章节。
