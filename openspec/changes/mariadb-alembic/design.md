# Design — MariaDB 脚本 + 迁移 + 集成

## 存储层（既有，复用）
- `AGENTBOARD_DB_URL` 单一入口：`sqlite://` 前缀决定是否加 `check_same_thread`；MariaDB 用 `mysql+pymysql`。
- `init_db()` 优先 `alembic upgrade head`；失败降级 `create_all` + `_ensure_migrations` 兜底。

## 独立 `.sql` 脚本（`scripts/mariadb/schema.sql`）
- 字符集 `utf8mb4` / 排序 `utf8mb4_unicode_ci`。
- 建库 `agentboard`、建应用用户并 `GRANT`（与 docker-compose 的 `MARIADB_USER/PASSWORD` 对齐）。
- 五表与 `models.py` 对齐：`projects / epics / stories / tasks / users`。
  - `tasks.source_spec_id`：`INTEGER NULL`（普通列，无外键，规避 ALTER 加 FK 的兼容问题，与 Alembic 迁移保持一致）。
  - `users.username`：`VARCHAR(64)` 唯一约束；`password_hash`：`VARCHAR(256)`。
  - 时间戳列 `DateTime` → MariaDB `DATETIME`；SQLite 与 MariaDB 均不存时区。
- 提供初始化说明：可由 `docker compose --profile mariadb up -d` 后的 entrypoint 执行，或 DBA 离线评审后手动执行。

## 双路径关系
- **离线脚本** `schema.sql`：用于评审 / 容器初始化 / 全新环境一键建库。
- **在线迁移** Alembic：用于已有库的演进（新增列 / 表）。二者 DDL 必须一致。

## 真实 MariaDB 验证
- `docker compose --profile mariadb up -d` 起 MariaDB 11。
- 设 `AGENTBOARD_DB_URL=mysql+pymysql://agentboard:agentboard@localhost:3306/agentboard` 跑 `init_db`（执行 Alembic `upgrade head`）。
- 跑 service 层冒烟：CRUD + 状态机 + 搜索 + 生成子任务。

## 集成测试（`tests/test_mariadb_integration.py`）
- `pytest.mark.skipif(not os.getenv("AGENTBOARD_TEST_MARIADB"), ...)`：仅当配置可用 MariaDB 时运行。
- 用 `AGENTBOARD_DB_URL` 指向 MariaDB，验证 `init_db` 建表、`source_spec_id` 列存在、service CRUD 通过。
