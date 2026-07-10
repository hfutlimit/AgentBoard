# Tasks — MariaDB 接入 + Alembic + MCP 工具补全

## 1. MariaDB 接入
- [ ] 用户提供可用的 MariaDB 连接信息（`AGENTBOARD_DB_URL=mysql+pymysql://...`）
- [ ] 用 SQLite 之外的 MariaDB 启动 `init_db`，验证建表 DDL 兼容（字符集/索引/外键）
- [ ] 在 MariaDB 下跑通 service 层冒烟（CRUD + 状态机 + 搜索 + 生成子任务）

## 2. Alembic 迁移
- [x] 初始化 `alembic.ini` 与 `migrations/`（env.py 复用 `AGENTBOARD_DB_URL`）
- [x] 编写初始迁移：projects / epics / stories / tasks（含 `source_spec_id`）
- [x] `database.init_db` 改为：有 Alembic 时执行 `alembic upgrade head`；否则保留 `create_all` + 在线迁移兜底
- [ ] 更新 smoke test 增加「可切换 MariaDB URL」分支（可选，需可用实例）

## 3. MCP 工具补全
- [x] `get_epic` / `get_story`（api + db 后端）
- [x] `update_epic` / `delete_epic` / `update_story` / `delete_story`（api + db 后端）
- [x] 列表类工具增加分页参数（limit / offset），REST 与 service 同步
- [x] 更新 `openspec/specs/agentboard/spec.md` 的 MCP 工具清单
