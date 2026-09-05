# Tasks: 支持 Epic 已阻塞状态持久化

- [x] 对齐 Epic ORM 模型与 `blocked` 状态契约。
- [x] 增加跨 SQLite/MariaDB 的 `ck_epics_status` 增量迁移。
- [x] 增加 `PATCH /api/epics/{id}` 设为 `blocked` 的持久化回归测试。
- [x] 运行 OpenSpec 严格校验、聚焦 pytest、Alembic upgrade/downgrade 和静态差异检查。
