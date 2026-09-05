# Proposal: 支持 Epic 已阻塞状态持久化

## Why

项目 Epic 工作台已发布“已阻塞”筛选选项，但 FastAPI 的共享状态校验接受
`blocked` 后，`epics.ck_epics_status` 仍拒绝该值。调用 `PATCH /api/epics/{id}`
会在数据库 flush 时抛出完整性错误并返回 HTTP 500，无法产生供筛选验证的真实数据。

## What Changes

- 将 Epic ORM 模型和数据库 `ck_epics_status` 约束扩展为允许 `blocked`。
- 增加 SQLite 与 MariaDB 均可执行的增量 Alembic 迁移。
- 回归覆盖 API 更新、HTTP 200 响应及持久化读取。

## Non-goals

- 不改变 Epic 列表的客户端筛选、排序或分页实现。
- 不新增 Epic 状态迁移规则、状态原因字段或服务端列表查询参数。
- 不修复与本缺陷无关的 Compose/.NET 本地部署问题。

## Impact

- `src/backend-fastapi/agentboard/features/projects/models.py`
- `src/backend-fastapi/migrations/versions/`
- `tests/test_epic_blocked_status.py`
