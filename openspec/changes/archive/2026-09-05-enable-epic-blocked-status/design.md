# Design: Epic 已阻塞状态持久化

## Decision

`blocked` 已是前端工作台公开的 Epic 筛选值，也是后端通用状态校验接受的值；本变更将
Epic 的模型级和数据库级状态集合与该既有契约对齐。`PATCH /api/epics/{id}` 保持现有
路由、请求体和序列化逻辑，不需要新增 API 字段。

## Migration

迁移在 SQLite 使用 `batch_alter_table` 重建 `epics` 的命名检查约束，在 MariaDB 使用
`drop_constraint` / `create_check_constraint` 直接替换。升级允许 `blocked`；降级恢复
此前六个状态的约束。因为升级只扩展允许集合，不需要数据回填。

## Verification

从全新 SQLite 数据库运行至 Alembic head，创建 Epic 后调用真实 FastAPI PATCH 路由。
断言 HTTP 200、响应状态和重新读取后的状态均为 `blocked`，以同时验证路由、服务、模型
和迁移后的数据库约束。
